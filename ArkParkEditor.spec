# -*- mode: python ; coding: utf-8 -*-
a = Analysis(['launcher.py'], pathex=[], binaries=[],
             datas=[('frontend/dist', 'frontend/dist'), ('backend/comfort_rules.json', 'backend'),
                    ('backend/gameplay_catalog.json', 'backend')],
             hiddenimports=['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
                            'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on'],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='ArkParkEditor',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
