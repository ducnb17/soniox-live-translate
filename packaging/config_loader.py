"""Load + persist the user config (API key, host, port).

This mirrors ``backend/app/config_store.py`` but lives in the packaging
package so the launcher can import it *before* the backend modules are
imported (and before ``dotenv`` runs).
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "SonioxLiveTranslate"


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


def load_user_config() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_user_config(cfg: dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def apply_env(cfg: dict[str, Any]) -> None:
    """Push the API key into the process environment so ``app.config`` picks
    it up via ``os.environ.get``."""
    key = cfg.get("soniox_api_key") or os.environ.get("SONIOX_API_KEY", "")
    if key:
        os.environ["SONIOX_API_KEY"] = key


def is_configured(cfg: dict[str, Any]) -> bool:
    key = cfg.get("soniox_api_key") or os.environ.get("SONIOX_API_KEY", "")
    return bool(key) and key != "your_key_here"
