"""Per-user persistent config (API key + server settings).

Location
--------
- Windows: %APPDATA%\\SonioxLiveTranslate\\config.json
- macOS:   ~/Library/Application Support/SonioxLiveTranslate/config.json
- Linux:   ${XDG_CONFIG_HOME:-~/.config}/soniox-live-translate/config.json

The launcher reads this *before* importing the app modules so the
``SONIOX_API_KEY`` env var is set in time. The ``/setup`` route writes here
on first-run submission and calls ``config.set_api_key`` so the running
process picks up the new key without a restart.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "SonioxLiveTranslate"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg: dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def get_api_key() -> str:
    return str(load_config().get("soniox_api_key", "") or "")


def is_configured() -> bool:
    key = get_api_key()
    return bool(key) and key != "your_key_here"
