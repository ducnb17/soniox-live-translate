# -*- mode: python ; coding: utf-8 -*-
# macOS build spec — .app bundle
import os, sys

ROOT          = os.path.abspath(os.path.join(SPECPATH, '..'))
BACKEND       = os.path.join(ROOT, 'backend')
FRONTEND_DIST = os.path.join(ROOT, 'frontend', 'dist')
LAUNCHER      = os.path.join(ROOT, 'installer', 'launcher_mac.py')
ICON          = os.path.join(ROOT, 'installer', 'icon.icns')

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

uvicorn_hiddenimports = [
    'uvicorn', 'uvicorn.server', 'uvicorn.config',
    'uvicorn.loops', 'uvicorn.loops.asyncio',
    'uvicorn.protocols', 'uvicorn.protocols.http',
    'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    'uvicorn.middleware', 'uvicorn.middleware.proxy_headers',
]

# Use icon.icns if available, fall back to icon.png
icon_file = ICON if os.path.exists(ICON) else os.path.join(ROOT, 'installer', 'icon.png')
datas_list = [(FRONTEND_DIST, 'frontend/dist')]
if os.path.exists(icon_file):
    datas_list.append((icon_file, '.'))

a = Analysis(
    [LAUNCHER],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=datas_list,
    hiddenimports=[
        *uvicorn_hiddenimports,
        'fastapi', 'starlette', 'starlette.routing', 'starlette.staticfiles',
        'starlette.responses', 'starlette.middleware',
        'starlette.middleware.cors', 'starlette.websockets',
        'anyio', 'anyio._backends._asyncio',
        'httpx', 'websockets', 'h11',
        'dotenv', 'structlog',
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
        'app.tts_providers.pocket_tts_provider', 'app.tts_providers.edge_provider',
        'aiosqlite',
        'sentry_sdk', 'sentry_sdk.integrations', 'sentry_sdk.integrations.fastapi',
        'sentry_sdk.integrations.starlette', 'sentry_sdk.integrations.logging',
        'sentry_sdk.integrations.asyncio',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'pytest',
              'win32crypt', 'pywintypes', 'pystray', 'webview'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='SonioxLiveTranslate',
    debug=False, strip=False, upx=True,
    console=False,
    icon=icon_file,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='SonioxLiveTranslate',
)

app = BUNDLE(
    coll,
    name='SonioxLiveTranslate.app',
    icon=icon_file,
    bundle_identifier='com.sonioxlivetranslate.app',
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleName': 'Soniox Live Translate',
        'CFBundleDisplayName': 'Soniox Live Translate',
        'NSMicrophoneUsageDescription': 'Soniox Live Translate needs microphone access for speech-to-speech translation.',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
