"""Server-only launcher bundled inside the Electron desktop application.

Electron owns the window, tray and application lifecycle.  This executable
only hosts the local FastAPI server, so it intentionally has no pywebview,
WinForms, CLR, pystray or Pillow dependency.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


class _NullStream:
    def write(self, *_args, **_kwargs):
        return 0

    def flush(self, *_args, **_kwargs):
        return None

    def isatty(self, *_args, **_kwargs):
        return False

    def fileno(self, *_args, **_kwargs):
        raise OSError("no fileno for null stream")


if sys.stdout is None:
    sys.stdout = _NullStream()  # type: ignore[assignment]
if sys.stderr is None:
    sys.stderr = _NullStream()  # type: ignore[assignment]
if sys.stdin is None:
    sys.stdin = _NullStream()  # type: ignore[assignment]


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "SonioxLiveTranslate"


def main() -> None:
    # Load the DPAPI-protected desktop configuration before importing app
    # modules so STT and TTS receive the current key on their first request.
    from app.config_store import load_config

    cfg = load_config()
    saved_key = cfg.get("soniox_api_key") or cfg.get("SONIOX_API_KEY")
    if saved_key:
        os.environ.setdefault("SONIOX_API_KEY", str(saved_key))

    log_dir = _config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "electron-backend.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    import uvicorn
    from app.main import app

    host = "127.0.0.1"
    # Keep this in lockstep with electron/main.js.  Electron owns the local
    # endpoint and always polls 127.0.0.1:8765 for readiness.
    port = 8765
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
