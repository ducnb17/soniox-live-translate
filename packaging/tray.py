"""System tray icon (pystray) — the foreground loop for the desktop app.

Menu:
- Open app      → webbrowser.open(BASE_URL)
- Settings      → webbrowser.open(BASE_URL/setup)
- Quit          → stops the tray, which exits the launcher.

We import pystray lazily so the launcher can run without it during dev
(python -m packaging.launcher falls back to a no-op tray).
"""

import threading
import webbrowser
from typing import Callable


def _make_icon_image():
    """Generate a simple 64x64 icon at runtime so we don't need an .ico file
    in the source tree. PyInstaller bundles Pillow too."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (37, 99, 235, 0))
    d = ImageDraw.Draw(img)
    # Rounded blue square
    d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=(37, 99, 235, 255))
    # White speech-bubble dot
    d.ellipse([20, 18, 44, 42], fill=(255, 255, 255, 255))
    d.rounded_rectangle([26, 40, 34, 50], radius=3, fill=(255, 255, 255, 255))
    return img


def run_tray(base_url: str, configured: bool) -> None:
    """Run the pystray loop on the calling (main) thread. Returns when the
    user picks Quit. Falls back to a blocking sleep loop if pystray is not
    installed (dev environment)."""
    try:
        import pystray
        from pystray import MenuItem, Menu
    except ImportError:
        print("[launcher] pystray not installed — running headless. Ctrl+C to quit.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    def on_open(icon, item):
        webbrowser.open(base_url)

    def on_settings(icon, item):
        webbrowser.open(f"{base_url}/setup")

    def on_quit(icon, item):
        icon.stop()

    image = _make_icon_image()
    menu = Menu(
        MenuItem("Open app", on_open, default=True),
        MenuItem("Settings", on_settings),
        Menu.SEPARATOR,
        MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("SonioxLiveTranslate", image, "Soniox Live Translate", menu)
    icon.run()
