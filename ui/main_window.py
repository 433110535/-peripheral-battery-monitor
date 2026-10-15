"""Main window -- a compact, vertical list with one row per device.

Nothing here talks to the hardware: the window only renders `DeviceStatus`
objects handed to it by the DeviceManager (via a Qt signal), so it never blocks
on a slow or missing device.

Layout (方案 C, compact):

    ┌──────────────────────────────────────┐
    │ Razer Viper V3 Pro White…      [Razer]│
    │ 🔋 使用电池 · 更新于 22:01:31    63 % │
    │ ▬▬▬▬▬▬▬▬▬▬▬▬░░░░░░░░░░░░░░░░░░░░░░░░░ │
    ├──────────────────────────────────────┤
    │ HyperX 耳机                   [HyperX]│
    │ ⚡ 充电中 · 更新于 22:01:26      91 % │
    │ ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬ │
    └──────────────────────────────────────┘
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# palette
_COL_TEXT = "#111827"
_COL_MUTED = "#6b7280"
_COL_FAINT = "#9ca3af"
_COL_LOW = "#ef4444"
_COL_WARN = "#b45309"
_COL_OK = "#22c55e"
_COL_CHARGING = "#16a34a"
_COL_BORDER = "#e5e7eb"
_COL_TRACK = "#eef0f3"
_COL_BG = "#f3f4f6"

_STYLESHEET = f"""
QWidget#root {{ background: {_COL_BG}; }}
QFrame#card {{
    background: #ffffff;
    border: 1px solid {_COL_BORDER};
    border-radius: 14px;
}}
QLabel#name {{ color: {_COL_TEXT}; font-size: 13px; font-weight: 600; }}
QLabel#brandChip {{
    color: {_COL_MUTED}; background: {_COL_BG}; border-radius: 8px;
    padding: 2px 8px; font-size: 10.5px;
}}
QLabel#sub {{ color: {_COL_MUTED}; font-size: 11.5px; }}
QLabel#num {{ color: {_COL_TEXT}; }}
QLabel#banner {{
    background: #fff7ed; color: #9a3412; border: 1px solid #fed7aa;
    border-radius: 10px; padding: 10px 12px; font-size: 12.5px;
}}
QFrame#divider {{ background: {_COL_TRACK}; border: none; }}
QProgressBar#bar {{ background: {_COL_TRACK}; border: none; border-radius: 3px; }}
QPushButton#refresh {{
    background: #ffffff; border: 1px solid {_COL_BORDER}; border-radius: 8px;
    padding: 6px 14px; color: {_COL_TEXT};
}}
QPushButton#refresh:hover {{ background: #f9fafb; }}
"""


def _bar_style(colour: str) -> str:
    return (f"QProgressBar#bar {{ background: {_COL_TRACK}; border: none;"
            f" border-radius: 3px; }}"
            f"QProgressBar#bar::chunk {{ background: {colour};"
            f" border-radius: 3px; }}")


def _short(text: str, limit: int = 16) -> str:
    """First clause of a message, trimmed so a row stays compact."""
    for sep in ("，", "。", ",", ";", "；"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _ElidedLabel(QLabel):
    """A label that ellipsizes its full text to whatever width it is given."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        # Ignored: never force the window wider because of a long name.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(90)
        self.setToolTip(text)

    def setFullText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        self.setText(metrics.elidedText(self._full, Qt.ElideRight,
                                        max(40, self.width())))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._elide()


class DeviceRow(QWidget):
    """One compact device entry: name + status on the left, big % on the right."""

    def __init__(self, brand: str, title: str, threshold: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.brand = brand
        self.threshold = threshold

        self._name = _ElidedLabel(title)
        self._name.setObjectName("name")
        self._chip = QLabel(brand)
        self._chip.setObjectName("brandChip")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(self._name, 1)
        header.addWidget(self._chip, 0)

        self._sub = QLabel("等待数据…")
        self._sub.setObjectName("sub")

        self._bar = QProgressBar()
        self._bar.setObjectName("bar")
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setValue(0)
        self._bar.setStyleSheet(_bar_style(_COL_FAINT))

        self._num = QLabel("--")
        self._num.setObjectName("num")

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(7)
        left.addWidget(self._sub)
        left.addWidget(self._bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.addLayout(left, 1)
        body.addWidget(self._num, 0, Qt.AlignVCenter)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addLayout(header)
        root.addLayout(body)

    # ------------------------------------------------------------------
    def set_status(self, status) -> None:
        if status.name:
            self._name.setFullText(status.name)

        battery = status.battery
        offline = not status.online
        low = battery is not None and battery < self.threshold

        # number + bar colour
        if battery is None or offline:
            number_colour = _COL_FAINT
            bar_colour = _COL_FAINT
        elif low:
            number_colour = _COL_LOW
            bar_colour = _COL_LOW
        elif status.charging:
            number_colour = _COL_CHARGING
            bar_colour = _COL_CHARGING
        else:
            number_colour = _COL_TEXT
            bar_colour = _COL_OK

        if battery is None:
            self._num.setText('<span style="font-size:26px;font-weight:600">--</span>')
            self._bar.setValue(0)
        else:
            self._num.setText(
                f'<span style="font-size:30px;font-weight:600">{battery}</span>'
                f'<span style="font-size:13px;font-weight:600">%</span>')
            self._bar.setValue(battery)
        self._num.setStyleSheet(f"color: {number_colour};")
        self._bar.setStyleSheet(_bar_style(bar_colour))

        self._sub.setText(self._sub_text(status, low, offline))
        self._sub.setStyleSheet(
            f"color: {_COL_LOW}; font-size: 11.5px;" if (low and not offline)
            else f"color: {_COL_WARN}; font-size: 11.5px;" if status.error and not offline
            else "color: %s; font-size: 11.5px;" % _COL_MUTED)

    @staticmethod
    def _sub_text(status, low: bool, offline: bool) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(status.last_update))
        if status.charging is True:
            parts = ["⚡ 充电中"]
        elif status.charging is False:
            parts = ["🔋 使用电池"]
        else:
            parts = []

        if low and status.online:
            parts.append("电量偏低")
        if status.error:
            # Keep the row compact; the banner below carries the full wording.
            parts.append(_short(status.error))
        if status.battery is None and not parts:
            parts.append("未连接")
        if not parts:
            parts.append("未知状态")
        return f"{' · '.join(parts)} · 更新于 {stamp}"


class MainWindow(QWidget):
    refresh_requested = Signal()

    def __init__(self, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("root")
        self.setWindowTitle("外设电量监控")
        self.setStyleSheet(_STYLESHEET)

        self._threshold = config.ui.low_battery_threshold
        self._rows: dict[str, DeviceRow] = {}
        self._latest: dict[str, object] = {}
        self._fit_pending = False

        self._card = QFrame()
        self._card.setObjectName("card")
        self._rows_layout = QVBoxLayout(self._card)
        self._rows_layout.setContentsMargins(16, 14, 16, 14)
        self._rows_layout.setSpacing(13)

        self._banner = QLabel("")
        self._banner.setObjectName("banner")
        # No word wrap: explicit short lines keep the needed height predictable,
        # so the window can size itself exactly.
        self._banner.setWordWrap(False)
        self._banner.hide()

        self._refresh = QPushButton("立即刷新")
        self._refresh.setObjectName("refresh")
        self._refresh.clicked.connect(self.refresh_requested.emit)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self._banner, 1)
        footer.addWidget(self._refresh, 0, Qt.AlignRight | Qt.AlignBottom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._card)
        layout.addLayout(footer)

        self.setMinimumWidth(380)
        self.resize(460, 240)

    # ------------------------------------------------------------------
    def _row_for(self, status) -> DeviceRow:
        row = self._rows.get(status.brand)
        if row is None:
            row = DeviceRow(status.brand, status.name or status.brand,
                            self._threshold)
            if self._rows:  # hairline between devices
                divider = QFrame()
                divider.setObjectName("divider")
                divider.setFixedHeight(1)
                self._rows_layout.addWidget(divider)
            self._rows_layout.addWidget(row)
            self._rows[status.brand] = row
        return row

    @Slot(object)
    def on_status(self, status) -> None:
        """Slot wired to the manager's update signal (runs on the GUI thread)."""
        self._latest[status.brand] = status
        self._row_for(status).set_status(status)
        self._update_banner()
        self._schedule_fit()

    def _update_banner(self) -> None:
        if not self._rows:
            return
        if any(s.online and s.battery is not None for s in self._latest.values()):
            self._banner.hide()
            return
        lines = [f"• {_short(s.error, 24)}"
                 for s in self._latest.values() if s.error]
        lines.append("请确认：")
        lines.append("  • Razer Synapse 正在运行")
        lines.append("  • HyperX 接收器已插入、耳机已开机")
        self._banner.setText("\n".join(lines))
        self._banner.show()

    def _schedule_fit(self) -> None:
        """Grow/shrink the window height to the content, debounced."""
        if self._fit_pending:
            return
        self._fit_pending = True
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        self._fit_pending = False
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    # --- window behaviour -------------------------------------------------
    def toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if getattr(self.config.ui, "close_to_tray", True):
            event.ignore()
            self.hide()
        else:
            event.accept()
