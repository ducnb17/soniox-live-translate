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


def get_tts_api_key(provider_id: str) -> str | None:
    cfg = load_config()
    keys = cfg.get("tts_api_keys", {})
    return keys.get(provider_id)


def set_tts_api_key(provider_id: str, key: str) -> None:
    cfg = load_config()
    if "tts_api_keys" not in cfg:
        cfg["tts_api_keys"] = {}
    cfg["tts_api_keys"][provider_id] = key
    save_config(cfg)


def remove_tts_api_key(provider_id: str) -> None:
    cfg = load_config()
    cfg.get("tts_api_keys", {}).pop(provider_id, None)
    save_config(cfg)


def get_tts_provider() -> str:
    return load_config().get("tts_provider", "soniox")


def set_tts_provider(provider_id: str) -> None:
    cfg = load_config()
    cfg["tts_provider"] = provider_id
    save_config(cfg)


def get_tts_voice(provider_id: str) -> str:
    cfg = load_config()
    voices = cfg.get("tts_voices", {})
    return voices.get(provider_id, "Maya" if provider_id == "soniox" else "")


def set_tts_voice(provider_id: str, voice: str) -> None:
    cfg = load_config()
    if "tts_voices" not in cfg:
        cfg["tts_voices"] = {}
    cfg["tts_voices"][provider_id] = voice
    save_config(cfg)
