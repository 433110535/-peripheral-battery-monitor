"""Generate the application icon (assets/app.ico) from the tray battery drawing.

The exe icon and the tray icon then come from the same code, so they always
match.

Usage (from the peripheral_battery folder):

    ..\\.venv\\Scripts\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.tray import make_battery_icon  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "assets")
SIZES = (256, 128, 64, 48, 32, 16)


def main() -> int:
    app = QApplication(sys.argv)  # a QGuiApplication is required for QPixmap
    os.makedirs(ASSETS, exist_ok=True)

    # 88% / not charging: a healthy-looking icon.
    icon = make_battery_icon(88, False, low_threshold=20, size=256)

    out = os.path.join(ASSETS, "app.ico")
    pixmap = icon.pixmap(256, 256)
    if not pixmap.save(out, "ICO"):
        print("failed to write", out)
        return 1
    print("wrote", out, pixmap.width(), "x", pixmap.height())

    # A PNG copy is handy for the README / shortcuts.
    png = os.path.join(ASSETS, "app.png")
    pixmap.save(png, "PNG")
    print("wrote", png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
