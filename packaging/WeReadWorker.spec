# -*- mode: python ; coding: utf-8 -*-
"""WeReadWorker: onedir + console (launched hidden by the GUI).

Frozen layout expected by weread_worker.py / weread_desktop.py:
  _MEIPASS/weread_desktop.py
  _MEIPASS/kindle_data/03_插件源码与解压文件/weread.koplugin/...
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
# The Lua plugin lives beside the Kindle project, two levels above the repo
# root: <Kindle>/05_补丁与其他/桌面端WeRead下载器 -> <Kindle>/03_插件源码与解压文件
# Both dirnames are needed: ROOT is the repo root itself, so one dirname only
# reaches <Kindle>/05_补丁与其他 and the build dies with "plugin dir not found".
PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(ROOT)),
    "03_插件源码与解压文件",
    "weread.koplugin",
)
if not os.path.isdir(PLUGIN_DIR):
    raise SystemExit("plugin dir not found: " + PLUGIN_DIR)

datas = [(os.path.join(SRC, "weread_desktop.py"), ".")]
datas.append((PLUGIN_DIR, os.path.join(
    "kindle_data", "03_插件源码与解压文件", "weread.koplugin")))
binaries = []
hiddenimports = [
    "lupa.luajit21",
    # weread_desktop.py is executed via runpy from a data file, so its own
    # imports are invisible to Analysis and must be declared here.
    "atexit", "ctypes", "subprocess", "threading",
    "requests.adapters", "urllib3.util.retry",
    "lxml.etree", "lxml.html",
    # --login mode: weread_login drives the QR protocol, qrcode renders the code
    # and PIL turns it into a PNG the web front end only streams through.
    "weread_login", "qrcode.image.pil", "PIL.Image", "PIL.ImageDraw",
]
tmp_ret = collect_all("lupa")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]
tmp_ret = collect_all("qrcode")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    [os.path.join(SRC, "weread_worker.py")],
    # The worker loads weread_desktop.py by path at runtime and imports the
    # sibling modules by bare name; the analysis path must name weread/ or the
    # static pass cannot see them.
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["customtkinter", "darkdetect", "tkinter", "_tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WeReadWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    # The worker is launched hidden, so nothing ever looks at this icon in
    # normal use. It is set anyway: an unset icon means the default Python
    # one, which shows up in Task Manager and in any crash report dialog.
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
    name="WeReadWorker",
)
