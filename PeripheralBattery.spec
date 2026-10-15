# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build config for the Peripheral Battery Monitor.

Build (from this folder):

    ..\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm PeripheralBattery.spec

Result:

    dist\\PeripheralBattery.exe      single file, no console window

Notes
-----
* ``main.py`` uses absolute imports (``import config``, ``from adapters ...``),
  so this project folder must be on ``pathex``.
* ``hid`` ships as a single ``.pyd`` with hidapi linked in, so it needs nothing
  special; ``watchdog`` loads its platform backend dynamically, hence the
  ``hiddenimports``.
* Config and logs live in ``%LOCALAPPDATA%\\PeripheralBattery``, so the exe can
  sit anywhere and still pick up an existing ``config.json``.
"""

import os

PROJECT_DIR = SPECPATH  # the folder holding this spec == the app package root

icon_path = os.path.join(PROJECT_DIR, 'assets', 'app.ico')
icon = icon_path if os.path.exists(icon_path) else None

a = Analysis(
    [os.path.join(PROJECT_DIR, 'main.py')],
    pathex=[PROJECT_DIR],
    binaries=[],
    datas=[],
    hiddenimports=[
        'hid',
        'watchdog.observers.winapi',
        'watchdog.observers.read_directory_changes',
        'watchdog.observers.polling',
        'watchdog.observers.api',
        'watchdog.utils.dirsnapshot',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc', 'doctest'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PeripheralBattery',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
