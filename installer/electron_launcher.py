"""Server-only launcher bundled inside the Electron desktop application.

Electron owns the window, tray and lifecycle. This process only hosts the
local FastAPI application; it intentionally has no pywebview runtime.
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
    # Read the protected desktop configuration before importing app.main so
    # the very first STT/TTS request sees the saved Soniox key.
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

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8765,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
