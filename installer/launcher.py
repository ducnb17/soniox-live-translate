"""
Soniox Live Translate — Windows desktop launcher.
Self-contained: NO imports from installer.* or packaging.*
"""
from __future__ import annotations
import os, sys, time, threading, logging
from pathlib import Path


# ── Fix stdout/stderr/stdin for windowed (console=False) PyInstaller apps ──
# When built with console=False there is no console, so Python sets
# sys.stdout/stderr/stdin to None. Many libraries (uvicorn's log color
# autodetection among them) call e.g. `sys.stdout.isatty()` unconditionally,
# which raises `AttributeError: 'NoneType' object has no attribute 'isatty'`.
# Replace the None streams with a harmless no-op stream before anything else
# (uvicorn, app.main, etc.) gets a chance to touch them.
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


# ── Path resolution (frozen PyInstaller exe vs dev) ───────────────────────

if getattr(sys, "frozen", False):
    # onedir: _MEIPASS == _internal/ dir next to the exe
    _MEIPASS = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    _BACKEND = _MEIPASS
    _ICON_PATH = _MEIPASS / "icon.ico"
else:
    _ROOT = Path(__file__).resolve().parent.parent
    _BACKEND = _ROOT / "backend"
    _ICON_PATH = _ROOT / "installer" / "icon.ico"
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
    # Use the same DPAPI-aware loader as the backend. This also migrates a
    # legacy plaintext config before the API key is copied into the process
    # environment.
    from app.config_store import load_config
    return load_config()


# ── Apply saved config → env BEFORE importing app modules ─────────────────
# NOTE: the canonical key name is the lowercase "soniox_api_key" — this is
# what `backend/app/config_store.py` and the `/setup` HTTP route read and
# write to this exact same config.json file. Historically this launcher
# looked for "SONIOX_API_KEY" (uppercase) instead, so a key saved via the
# in-browser /setup page was never picked up on the next launch, forcing
# the user back to /setup every time. Accept both spellings so old config
# files (if any) keep working too.
_cfg = _load_cfg()
_saved_api_key = _cfg.get("soniox_api_key") or _cfg.get("SONIOX_API_KEY")
if _saved_api_key:
    os.environ.setdefault("SONIOX_API_KEY", _saved_api_key)


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


# ── About dialog ──────────────────────────────────────────────────────────
def _show_about(icon) -> None:
    """Display a simple About message box."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Soniox Live Translate — © Nguyễn Bá Đức",
                "About",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            icon.notify("Soniox Live Translate — © Nguyễn Bá Đức", title="About")
    else:
        icon.notify("Soniox Live Translate — © Nguyễn Bá Đức", title="About")


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


def _run_tray(stop: threading.Event, window, quitting: threading.Event) -> None:
    import pystray

    def quit_app(icon) -> None:
        quitting.set()
        try:
            window.destroy()
        finally:
            stop.set()
            icon.stop()

    def open_settings() -> None:
        window.load_url(f"{BASE_URL}/setup")
        window.show()

    icon = pystray.Icon(
        "SonioxLiveTranslate",
        _make_icon(),
        "Soniox Live Translate",
        menu=pystray.Menu(
            pystray.MenuItem("Open",      lambda: window.show()),
            pystray.MenuItem("Settings",  lambda: open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("About",     lambda: _show_about(icon)),
            pystray.MenuItem("Quit",      lambda: quit_app(icon)),
        ),
    )
    threading.Thread(target=lambda: (stop.wait(), icon.stop()), daemon=True).start()
    icon.run()


# ── Fatal-error reporting (never fail silently) ───────────────────────────
def _report_fatal(context: str, exc: BaseException) -> None:
    import traceback
    tb = traceback.format_exc()
    log.error("%s failed: %s\n%s", context, exc, tb)
    if sys.platform == "win32":
        try:
            import ctypes
            msg = (
                f"Soniox Live Translate failed to start.\n\n"
                f"{context}: {exc}\n\n"
                f"Details were written to:\n{_log_dir / 'app.log'}"
            )
            ctypes.windll.user32.MessageBoxW(None, msg, "Soniox Live Translate — Startup Error", 0x10)
        except Exception:
            pass


# ── uvicorn server ────────────────────────────────────────────────────────
def _run_server(stop: threading.Event) -> None:
    try:
        import uvicorn
        from app.main import app                # noqa: PLC0415 — after sys.path
    except Exception as exc:
        _report_fatal("Server import", exc)
        stop.set()
        return
    try:
        cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
        srv = uvicorn.Server(cfg)
        threading.Thread(target=lambda: (stop.wait(), setattr(srv, "should_exit", True)),
                         daemon=True).start()
        srv.run()
    except Exception as exc:
        _report_fatal("Server run", exc)
        stop.set()


# ── Entry point ───────────────────────────────────────────────────────────
def main() -> None:
    stop = threading.Event()
    api_key  = os.environ.get("SONIOX_API_KEY", "")
    start_url = BASE_URL if api_key else f"{BASE_URL}/setup"

    threading.Thread(target=_run_server, args=(stop,), daemon=True).start()

    if not _wait_ready():
        if not stop.is_set():
            # _run_server didn't hit its own except branch (which already
            # reported), so this is a plain startup timeout — report it too.
            _report_fatal("Startup", RuntimeError("server did not become ready within 30s"))
        sys.exit(1)

    log.info("server ready  port=%d  url=%s", PORT, start_url)

    # Electron owns the window and tray; this process only hosts the server.
    if os.environ.get("ELECTRON_HOST"):
        stop.wait()
        log.info("launcher exit (electron mode)")
        return

    import webview
    window = webview.create_window(
        "Soniox Live Translate",
        start_url,
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    quitting = threading.Event()

    def on_closing() -> bool:
        if quitting.is_set():
            return True
        window.hide()
        return False

    window.events.closing += on_closing
    tray_thread = threading.Thread(
        target=_run_tray,
        args=(stop, window, quitting),
        daemon=True,
    )
    tray_thread.start()
    try:
        webview.start(icon=str(_ICON_PATH))
    finally:
        stop.set()
    log.info("launcher exit")



if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Last-resort catch-all: guarantees the user always sees *something*
        # instead of the exe silently vanishing.
        _report_fatal("Launcher", exc)
        sys.exit(1)
