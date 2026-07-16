"""Launcher entry point for the Windows desktop build.

Flow
----
1. Load %APPDATA%/SonioxLiveTranslate/config.json (if any).
2. If no API key configured, open browser at /setup and wait for it.
3. Start uvicorn (host/port from config or defaults).
4. Open the browser at the app URL.
5. Run the pystray system-tray icon (Open / Settings / Quit) — this is the
   foreground loop and keeps the process alive.

PyInstaller entry: `pyinstaller packaging/spec.spec` produces
``dist/SonioxLiveTranslate/SonioxLiveTranslate.exe`` which runs this module.

Run directly during development:
    python -m packaging.launcher            # uses .env / %APPDATA% config
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# --- Make `app` importable whether launched from frozen .exe or from source --
_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# --- Load persisted config BEFORE importing app modules so env is set -------
from packaging.config_loader import load_user_config, apply_env, is_configured  # noqa: E402

USER_CFG = load_user_config()
apply_env(USER_CFG)

HOST = USER_CFG.get("host", "127.0.0.1")
PORT = int(USER_CFG.get("port", 8765))
BASE_URL = f"http://{HOST}:{PORT}"


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """Block until the FastAPI server answers /health."""
    import httpx
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    print(f"[launcher] server did not start within {timeout}s: {last_err}", flush=True)
    return False


def main() -> None:
    import uvicorn
    from packaging.tray import run_tray

    configured = is_configured(USER_CFG)

    # Start uvicorn in a daemon thread so the tray loop can run on main.
    server_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": "app.main:app",
            "host": HOST,
            "port": PORT,
            "log_level": "warning",
        },
        daemon=True,
    )
    server_thread.start()

    if not _wait_for_server(BASE_URL):
        # Server failed — keep the console (if any) open so the user can read
        # the error before the window disappears.
        time.sleep(10)
        sys.exit(1)

    if not configured:
        webbrowser.open(f"{BASE_URL}/setup")
    else:
        webbrowser.open(BASE_URL)

    # Block on the tray loop until the user selects Quit.
    run_tray(base_url=BASE_URL, configured=configured)


if __name__ == "__main__":
    main()
