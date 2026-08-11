# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = []
hiddenimports = ['yt_dlp_ejs', 'yt_dlp_plugins.extractor.getpot_bgutil', 'yt_dlp_plugins.extractor.getpot_bgutil_http', 'yt_dlp_plugins.extractor.getpot_bgutil_script']
datas += collect_data_files('yt_dlp_ejs')
hiddenimports += collect_submodules('yt_dlp')
hiddenimports += collect_submodules('yt_dlp_plugins')


a = Analysis(
    ['C:/Users/Administrator/Desktop/yt_downloader-main/yt_downloader-main/source-code/yt_dlp_gui.py'],
    pathex=[],
    binaries=[],
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
    [],
    exclude_binaries=True,
    name='yt_dlp_gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    upx=False,
    upx_exclude=[],
    name='yt_dlp_gui',
)
