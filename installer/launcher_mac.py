"""
Soniox Live Translate — macOS launcher.
Opens the backend server then launches the default browser.
No pywebview required — works as a standard .app bundle.
"""
from __future__ import annotations
import os, sys, time, threading, subprocess, logging
from pathlib import Path


class _NullStream:
    def write(self, *_a, **_k): return 0
    def flush(self, *_a, **_k): pass
    def isatty(self, *_a, **_k): return False
    def fileno(self, *_a, **_k): raise OSError("no fileno for null stream")


if sys.stdout is None:
    sys.stdout = _NullStream()  # type: ignore[assignment]
if sys.stderr is None:
    sys.stderr = _NullStream()  # type: ignore[assignment]
if sys.stdin is None:
    sys.stdin = _NullStream()  # type: ignore[assignment]


# ── Path resolution ────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _MEIPASS = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    _BACKEND = _MEIPASS
else:
    _ROOT = Path(__file__).resolve().parent.parent
    _BACKEND = _ROOT / "backend"
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))


def _config_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "SonioxLiveTranslate"


def _load_cfg() -> dict:
    from app.config_store import load_config
    return load_config()


# ── Apply saved config → env ───────────────────────────────────────────────
_cfg = _load_cfg()
_saved_api_key = _cfg.get("soniox_api_key") or _cfg.get("SONIOX_API_KEY")
if _saved_api_key:
    os.environ.setdefault("SONIOX_API_KEY", _saved_api_key)

HOST = "127.0.0.1"
PORT = int(_cfg.get("PORT", 8766))
BASE_URL = f"http://{HOST}:{PORT}"

# ── Logging ────────────────────────────────────────────────────────────────
_log_dir = _config_dir() / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(_log_dir / "app.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("launcher_mac")


def _wait_ready(timeout: float = 30.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{BASE_URL}/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.6)
    return False


def _run_server(stop: threading.Event) -> None:
    try:
        import uvicorn
        from app.main import app
    except Exception as exc:
        log.error("Server import failed: %s", exc)
        stop.set()
        return
    try:
        cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
        srv = uvicorn.Server(cfg)
        threading.Thread(
            target=lambda: (stop.wait(), setattr(srv, "should_exit", True)),
            daemon=True,
        ).start()
        srv.run()
    except Exception as exc:
        log.error("Server run failed: %s", exc)
        stop.set()


def _open_browser(url: str) -> None:
    """Open the app URL in the default browser."""
    try:
        subprocess.Popen(["open", url])
    except Exception as exc:
        log.error("Failed to open browser: %s", exc)


def main() -> None:
    stop = threading.Event()
    threading.Thread(target=_run_server, args=(stop,), daemon=True).start()

    if not _wait_ready():
        log.error("Server did not become ready within 30s")
        # Show error via osascript
        subprocess.run([
            "osascript", "-e",
            'display alert "Soniox Live Translate" message '
            '"Failed to start the backend server. Check ~/Library/Application Support/SonioxLiveTranslate/logs/app.log for details."'
        ])
        sys.exit(1)

    api_key = os.environ.get("SONIOX_API_KEY", "")
    start_url = BASE_URL if api_key else f"{BASE_URL}/setup"
    log.info("Server ready port=%d url=%s", PORT, start_url)
    _open_browser(start_url)

    # Keep running until process is killed (Cmd+Q from Dock or Activity Monitor).
    stop.wait()
    log.info("Launcher exit")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("Launcher fatal: %s", exc, exc_info=True)
        sys.exit(1)
