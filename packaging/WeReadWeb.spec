# -*- mode: python ; coding: utf-8 -*-
"""WeReadDesktop web mode: onedir + windowed (no console flash), stdlib only.

The browser draws the UI, so this bundle must contain no GUI or imaging native
extension. A frozen package carrying PIL / tkinter / _tkinter / tcl86t.dll /
tk86t.dll gets held in a multi-second antivirus deep scan before its first line
of user code runs -- measured 5.2-6.4 s that way versus 0.108 s for a plain
stdlib http.server -- and avoiding that is the entire reason this mode exists.

The download engine ships in the sibling WeReadWorker bundle and is still driven
over UTF-8 stdio, so this exe stays free of LuaJIT/lxml and of every WeRead
credential.

Layout produced by --distpath release-web:
  release-web/WeReadDesktop/{WeReadDesktop.exe,_internal/}
  release-web/WeReadWorker/{WeReadWorker.exe,_internal/}
"""
import os

from PyInstaller.utils.hooks import collect_all

# SPECPATH is the directory holding this .spec file (packaging/), so the
# repository root is its parent and the program modules live in weread/.
ROOT = os.path.dirname(SPECPATH)
SRC = os.path.join(ROOT, "weread")
FAVICON = os.path.join(ROOT, "assets", "weread-web.ico")
if not os.path.isfile(FAVICON):
    raise SystemExit("favicon not found: " + FAVICON)

a = Analysis(
    [os.path.join(SRC, "weread_web.py")],
    # The modules import each other by bare name (import weread_core); the file
    # appends its own directory to sys.path at runtime, but PyInstaller's static
    # analysis does not execute code, so the path must be declared here or the
    # sibling modules are left out of the bundle.
    pathex=[SRC],
    binaries=[],
    datas=[(os.path.join(SRC, "webui.html"), "."), (FAVICON, ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Nothing below is reachable from weread_web.py; naming them keeps the
        # acceptance check (_internal free of PIL / tkinter / Tcl DLLs) true even
        # if a future hook starts reaching sideways.
        "tkinter", "_tkinter", "PIL", "customtkinter", "darkdetect",
        "lupa", "lupa.luajit21", "lxml", "lxml.etree", "lxml.html",
        "lxml._elementpath", "weread_desktop", "weread_worker",
        "requests", "urllib3", "certifi", "idna", "charset_normalizer",
        "numpy", "scipy", "pandas", "psutil", "cryptography", "nacl",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib",
        "IPython", "pytest", "setuptools", "pip", "sqlite3",
        "test", "unittest", "pydoc", "doctest",
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
    # "WeReadWeb", not "WeReadDesktop": both bundles land in {app} side by side,
    # and two programs with the same exe name in sibling directories is how a
    # shortcut ends up launching the wrong one -- the desktop icon and the start
    # menu entry cannot tell them apart by path alone.
    name="WeReadWeb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    # Same reason as the GUI spec: without this the exe shows PyInstaller's
    # default icon in Explorer, on the taskbar and in the title bar.
    icon=FAVICON,
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
    name="WeReadWeb",
)
