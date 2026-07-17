"""
Soniox Live Translate — Windows desktop launcher.
Self-contained: NO imports from installer.* or packaging.*
"""
from __future__ import annotations
import os, sys, json, time, threading, webbrowser, logging
from pathlib import Path


# ── Path resolution (frozen PyInstaller exe vs dev) ───────────────────────
if getattr(sys, "frozen", False):
    # onedir: _MEIPASS == _internal/ dir next to the exe
    _MEIPASS = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    _BACKEND = _MEIPASS
else:
    _ROOT = Path(__file__).resolve().parent.parent
    _BACKEND = _ROOT / "backend"
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))


# ── Inline config store (cross-platform AppData) ──────────────────────────
def _config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "SonioxLiveTranslate"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _load_cfg() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Apply saved config → env BEFORE importing app modules ─────────────────
_cfg = _load_cfg()
if _cfg.get("SONIOX_API_KEY"):
    os.environ.setdefault("SONIOX_API_KEY", _cfg["SONIOX_API_KEY"])

HOST = "127.0.0.1"
PORT = int(_cfg.get("PORT", 8765))
BASE_URL = f"http://{HOST}:{PORT}"


# ── File logging (no console in GUI app) ──────────────────────────────────
_log_dir = _config_dir() / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(_log_dir / "app.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("launcher")


# ── Health poll ───────────────────────────────────────────────────────────
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


# ── System tray icon ──────────────────────────────────────────────────────
def _make_icon():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (37, 99, 235, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([16, 10, 48, 42], outline="white", width=5)
    d.rectangle([28, 42, 36, 54], fill="white")
    d.arc([10, 30, 54, 58], 180, 0, fill="white", width=5)
    d.line([32, 54, 32, 62], fill="white", width=5)
    d.line([22, 62, 42, 62], fill="white", width=5)
    return img


def _run_tray(stop: threading.Event) -> None:
    import pystray
    icon = pystray.Icon(
        "SonioxLiveTranslate",
        _make_icon(),
        "Soniox Live Translate",
        menu=pystray.Menu(
            pystray.MenuItem("Open",      lambda: webbrowser.open(BASE_URL)),
            pystray.MenuItem("Settings",  lambda: webbrowser.open(f"{BASE_URL}/setup")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",      lambda: (stop.set(), icon.stop())),
        ),
    )
    icon.run()


# ── uvicorn server ────────────────────────────────────────────────────────
def _run_server(stop: threading.Event) -> None:
    import uvicorn
    from app.main import app                    # noqa: PLC0415 — after sys.path
    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
    srv = uvicorn.Server(cfg)
    threading.Thread(target=lambda: (stop.wait(), setattr(srv, "should_exit", True)),
                     daemon=True).start()
    srv.run()


# ── Entry point ───────────────────────────────────────────────────────────
def main() -> None:
    stop = threading.Event()
    api_key  = os.environ.get("SONIOX_API_KEY", "")
    start_url = BASE_URL if api_key else f"{BASE_URL}/setup"

    threading.Thread(target=_run_server, args=(stop,), daemon=True).start()

    if not _wait_ready():
        log.error("server failed to start within 30s")
        sys.exit(1)

    log.info("server ready  port=%d  url=%s", PORT, start_url)
    threading.Thread(target=lambda: (time.sleep(0.5), webbrowser.open(start_url)),
                     daemon=True).start()
    _run_tray(stop)
    log.info("launcher exit")


if __name__ == "__main__":
    main()
