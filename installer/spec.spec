# -*- mode: python ; coding: utf-8 -*-
import os, sys

# SPECPATH = installer/ (directory containing this .spec file)
ROOT          = os.path.abspath(os.path.join(SPECPATH, '..'))
BACKEND       = os.path.join(ROOT, 'backend')
FRONTEND_DIST = os.path.join(ROOT, 'frontend', 'dist')
LAUNCHER      = os.path.join(ROOT, 'installer', 'launcher.py')
ICON          = os.path.join(ROOT, 'installer', 'icon.ico')

# CI copies backend/app → ROOT before running PyInstaller.
# In dev, add ROOT+BACKEND so 'import app' resolves.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

a = Analysis(
    [LAUNCHER],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[(FRONTEND_DIST, 'frontend/dist'), (ICON, '.')],
    hiddenimports=[
        # uvicorn
        'uvicorn', 'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off',
        # web framework
        'fastapi', 'starlette', 'starlette.routing', 'starlette.staticfiles',
        'starlette.responses', 'starlette.middleware',
        'starlette.middleware.cors', 'starlette.websockets',
        'anyio', 'anyio._backends._asyncio',
        # http / ws
        'httpx', 'websockets', 'h11',
        # config / logging
        'dotenv', 'structlog', 'win32crypt', 'pywintypes',
        # tray / icon
        'pystray', 'PIL', 'PIL.Image', 'PIL.ImageDraw',
        # desktop webview
        'webview', 'webview.platforms.winforms',
        'webview.platforms.edgechromium', 'clr',
        # app
        'app', 'app.main', 'app.config', 'app.config_store',
        'app.stt', 'app.tts', 'app.context_builder', 'app.transcript',
        'app.logging_config', 'app.db', 'app.provider_connection', 'app.version',
        'app.stt_provider', 'app.translation_provider',
        'app.tts_provider', 'app.external_tts',
        'app.stt_providers', 'app.stt_providers.soniox_provider',
        'app.stt_providers.openai_provider', 'app.stt_providers.deepgram_provider',
        'app.stt_providers.google_provider', 'app.stt_providers.assemblyai_provider',
        'app.translation_providers', 'app.translation_providers.soniox_provider',
        'app.translation_providers.google_provider', 'app.translation_providers.deepl_provider',
        'app.translation_providers.openai_provider',
        'app.tts_providers', 'app.tts_providers.soniox_provider',
        'app.tts_providers.google_provider', 'app.tts_providers.openai_provider',
        'app.tts_providers.azure_provider', 'app.tts_providers.elevenlabs_provider',
        'app.tts_providers.deepgram_provider', 'app.tts_providers.polly_provider',
        'aiosqlite',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='SonioxLiveTranslate',
    debug=False, strip=False, upx=True,
    console=False,
    icon=ICON,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='SonioxLiveTranslate',
)
