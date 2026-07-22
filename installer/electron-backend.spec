# -*- mode: python ; coding: utf-8 -*-
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND_DIST = os.path.join(ROOT, "frontend", "dist")
LAUNCHER = os.path.join(ROOT, "installer", "electron_launcher.py")
ICON = os.path.join(ROOT, "installer", "icon.ico")

for path in (ROOT, BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)

a = Analysis(
    [LAUNCHER],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[(FRONTEND_DIST, "frontend/dist"), (ICON, ".")],
    hiddenimports=[
        "uvicorn", "uvicorn.logging",
        "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
        "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
        "fastapi", "starlette", "starlette.routing", "starlette.staticfiles",
        "starlette.responses", "starlette.middleware", "starlette.middleware.cors",
        "starlette.websockets", "anyio", "anyio._backends._asyncio",
        "httpx", "websockets", "h11", "dotenv", "structlog",
        "win32crypt", "pywintypes", "aiosqlite",
        "app", "app.main", "app.config", "app.config_store", "app.stt", "app.tts",
        "app.context_builder", "app.transcript", "app.logging_config", "app.db",
        "app.provider_connection", "app.version", "app.stt_provider",
        "app.translation_provider", "app.tts_provider", "app.external_tts",
        "app.tts_session", "app.stt_providers", "app.stt_providers.soniox_provider",
        "app.stt_providers.openai_provider", "app.stt_providers.deepgram_provider",
        "app.stt_providers.google_provider", "app.stt_providers.assemblyai_provider",
        "app.translation_providers", "app.translation_providers.soniox_provider",
        "app.translation_providers.google_provider", "app.translation_providers.deepl_provider",
        "app.translation_providers.openai_provider", "app.tts_providers",
        "app.tts_providers.soniox_provider", "app.tts_providers.google_provider",
        "app.tts_providers.openai_provider", "app.tts_providers.azure_provider",
        "app.tts_providers.elevenlabs_provider", "app.tts_providers.deepgram_provider",
        "app.tts_providers.polly_provider",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "webview", "clr", "pystray", "PIL", "tkinter", "matplotlib",
        "numpy", "scipy", "pandas", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="SonioxLiveTranslate", debug=False, strip=False, upx=True,
    console=False, icon=ICON,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[], name="SonioxLiveTranslate",
)
