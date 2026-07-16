# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Soniox Live Translate desktop build.

Build (on Windows):
    cd <repo>
    pip install -r backend/requirements.txt
    pip install pyinstaller pystray pillow
    pyinstaller packaging/spec.spec --noconfirm

Output: dist/SonioxLiveTranslate/SonioxLiveTranslate.exe (onedir)
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# uvicorn uses lots of lazy imports that PyInstaller can't see statically.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("websockets")
    + collect_submodules("httpx")
    + collect_submodules("anyio")
    + collect_submodules("h11")
    + [
        "dotenv",
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "app",
        "app.main",
        "app.config",
        "app.config_store",
        "app.context_builder",
        "app.stt",
        "app.tts",
        "app.transcript",
        "packaging.launcher",
        "packaging.tray",
        "packaging.config_loader",
    ]
)

# Bundle the Vite-built frontend (HTML/CSS/JS) so StaticFiles can serve it
# from the frozen tree. The build script runs `pnpm build` first.
datas = [
    ("../frontend/dist", "frontend/dist"),
]

a = Analysis(
    ["launcher.py"],
    pathex=["../backend", "."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "pytest",
        "tkinter",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SonioxLiveTranslate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app — no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico" if __import__("os").path.exists("icon.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SonioxLiveTranslate",
)
