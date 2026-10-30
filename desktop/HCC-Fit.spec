# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=['.', 'core'],
    binaries=[],
    datas=[('resources', 'resources')],
    hiddenimports=['brains', 'planner', 'sciencepal', 'contract', 'hcc_report', 'pdftext', 'mock_sciencepal', 'input_normalization', 'ai_extract', 'input_store', 'input_review', 'supplement', 'supplement_store', 'router_client', 'router_dialog'],
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
    name='HCC-Fit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='HCC-Fit',
)
