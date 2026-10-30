# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    # ../router 及其 toolkit 子目录进搜索路径，让内置 Router 被静态分析（依赖才会被完整收集）
    pathex=['.', 'core', '../router',
            '../router/hccfit/toolkit', '../router/hccfit/toolkit/lib'],
    binaries=[],
    datas=[('resources', 'resources')],
    hiddenimports=[
        'brains', 'planner', 'sciencepal', 'contract', 'hcc_report', 'pdftext',
        'input_normalization', 'input_store', 'input_review', 'supplement',
        'supplement_store', 'router_client', 'router_dialog', 'router_engine',
        'requests',
        # 内置 Router（app/router_engine.py 延迟导入）
        'hccfit', 'hccfit.config', 'hccfit.scope', 'hccfit.sciencepal',
        'hccfit.core', 'hccfit.strategies',
        'run', 'validate', 'hcc_agent', 'multi_search', 'paper_search', 'sources',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['fastapi', 'uvicorn', 'pydantic'],
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
