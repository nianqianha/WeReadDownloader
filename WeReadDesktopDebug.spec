# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/asus/Desktop/各类/Kindle/05_补丁与其他/桌面端WeRead下载器/weread_desktop.py', '.'), ('C:/Users/asus/Desktop/各类/Kindle/03_插件源码与解压文件/weread.koplugin', 'kindle_data/03_插件源码与解压文件/weread.koplugin')]
binaries = []
hiddenimports = ['lupa.luajit21']
tmp_ret = collect_all('lupa')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/asus/Desktop/各类/Kindle/05_补丁与其他/桌面端WeRead下载器/desktop_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='WeReadDesktopDebug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
