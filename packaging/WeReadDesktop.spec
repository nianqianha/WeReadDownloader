# -*- mode: python ; coding: utf-8 -*-
"""WeReadDesktop GUI: onedir + windowed (no console flash).

The download engine ships in the sibling WeReadWorker bundle; this exe must
stay free of LuaJIT/lxml and of any WeRead credentials, so it starts fast and
never allocates a console window.
"""
import os

from PyInstaller.utils.hooks import collect_all

# SPECPATH is the directory holding this .spec file (packaging/), so the
# repository root is its parent and the program modules live in weread/.
ROOT = os.path.dirname(SPECPATH)
SRC = os.path.join(ROOT, "weread")
ICON = os.path.join(ROOT, "assets", "weread-app.ico")
if not os.path.isfile(ICON):
    raise SystemExit("icon not found: " + ICON)

datas = []
binaries = []
hiddenimports = []
for package in ("customtkinter", "darkdetect", "qrcode"):
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

a = Analysis(
    [os.path.join(SRC, "desktop_app.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The GUI never imports the export engine; keep LuaJIT/lxml and the
        # Lua plugin data out of the windowed bundle. tkinter/PIL stay in.
        "lupa", "lupa.luajit21", "lxml", "lxml.etree", "lxml.html",
        "lxml._elementpath", "weread_desktop", "weread_worker",
        # Never imported by this GUI, but pulled in transitively by the
        # customtkinter/PIL hooks. They cost ~50 MB of dead weight, and the
        # frozen interpreter pays for them on every launch: measured 5.5-6.1 s
        # to first window with them bundled versus 0.59 s without.
        "numpy", "scipy", "pandas", "psutil", "cryptography", "nacl",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib",
        "IPython", "pytest", "setuptools", "pip", "sqlite3",
        "PIL.ImageQt", "PIL.ImageShow", "PIL.ImageGrab",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WeReadDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    # The exe carries its own icon: this is what Explorer shows for the file,
    # the taskbar button and the title bar. Leaving it unset shipped the
    # PyInstaller default Python icon, which is why the window never matched
    # the desktop shortcut.
    icon=ICON,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WeReadDesktop",
)
