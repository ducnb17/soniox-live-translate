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
import base64
import copy
from pathlib import Path
from typing import Any

APP_NAME = "SonioxLiveTranslate"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DPAPI_PREFIX = "dpapi:v1:"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretProtectionError(RuntimeError):
    """Raised when a secret cannot be protected for the current Windows user."""


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


def _protect_secret(value: str) -> str:
    if value.startswith(DPAPI_PREFIX):
        return value
    if sys.platform != "win32":
        raise SecretProtectionError("Windows DPAPI is required to store API keys")
    try:
        import win32crypt

        encrypted = win32crypt.CryptProtectData(
            value.encode("utf-8"),
            APP_NAME,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
        )
    except Exception as exc:
        raise SecretProtectionError("Could not encrypt API key with Windows DPAPI") from exc
    return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")


def _unprotect_secret(value: str) -> str:
    if not value.startswith(DPAPI_PREFIX):
        return value
    if sys.platform != "win32":
        raise SecretProtectionError("Windows DPAPI is required to read API keys")
    try:
        import win32crypt

        encrypted = base64.b64decode(value[len(DPAPI_PREFIX):], validate=True)
        _description, plaintext = win32crypt.CryptUnprotectData(
            encrypted,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
        )
        return plaintext.decode("utf-8")
    except Exception as exc:
        raise SecretProtectionError(
            "Could not decrypt API key; it may belong to another Windows user account"
        ) from exc


def _secret_values(cfg: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    values: list[tuple[dict[str, Any], str]] = []
    for key_name in ("soniox_api_key", "SONIOX_API_KEY"):
        if cfg.get(key_name):
            values.append((cfg, key_name))
    for collection_name in ("tts_api_keys", "stt_api_keys", "translation_api_keys"):
        keys = cfg.get(collection_name)
        if isinstance(keys, dict):
            values.extend((keys, str(provider_id)) for provider_id, value in keys.items() if value)
    return values


def _encrypt_config(cfg: dict[str, Any]) -> dict[str, Any]:
    encrypted = copy.deepcopy(cfg)
    for container, key in _secret_values(encrypted):
        container[key] = _protect_secret(str(container[key]))
    return encrypted


def _decrypt_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    decrypted = copy.deepcopy(cfg)
    plaintext_found = False
    for container, key in _secret_values(decrypted):
        value = str(container[key])
        if value.startswith(DPAPI_PREFIX):
            container[key] = _unprotect_secret(value)
        else:
            plaintext_found = True
    return decrypted, plaintext_found


def _write_raw_config(cfg: dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(p)


def load_config() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    decrypted, plaintext_found = _decrypt_config(data)
    if plaintext_found:
        # One-time migration of legacy plaintext values. The atomic replace
        # keeps the original file intact if DPAPI encryption fails.
        _write_raw_config(_encrypt_config(decrypted))
    return decrypted


def save_config(cfg: dict[str, Any]) -> None:
    _write_raw_config(_encrypt_config(cfg))


def get_api_key() -> str:
    cfg = load_config()
    return str(cfg.get("soniox_api_key") or cfg.get("SONIOX_API_KEY") or "")


def is_configured() -> bool:
    key = get_api_key()
    return bool(key) and key != "your_key_here"


def get_tts_api_key(provider_id: str) -> str | None:
    cfg = load_config()
    keys = cfg.get("tts_api_keys", {})
    return keys.get(provider_id) if isinstance(keys, dict) else None


def set_tts_api_key(provider_id: str, key: str) -> None:
    cfg = load_config()
    if not isinstance(cfg.get("tts_api_keys"), dict):
        cfg["tts_api_keys"] = {}
    cfg["tts_api_keys"][provider_id] = key
    save_config(cfg)


def remove_tts_api_key(provider_id: str) -> None:
    cfg = load_config()
    keys = cfg.get("tts_api_keys")
    if isinstance(keys, dict):
        keys.pop(provider_id, None)
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
    if not isinstance(voices, dict):
        return "Maya" if provider_id == "soniox" else ""
    return voices.get(provider_id, "Maya" if provider_id == "soniox" else "")


def set_tts_voice(provider_id: str, voice: str) -> None:
    cfg = load_config()
    if not isinstance(cfg.get("tts_voices"), dict):
        cfg["tts_voices"] = {}
    cfg["tts_voices"][provider_id] = voice
    save_config(cfg)


def _get_provider_api_key(collection: str, provider_id: str) -> str | None:
    keys = load_config().get(collection, {})
    return keys.get(provider_id) if isinstance(keys, dict) else None


def _set_provider_api_key(collection: str, provider_id: str, key: str) -> None:
    cfg = load_config()
    if not isinstance(cfg.get(collection), dict):
        cfg[collection] = {}
    cfg[collection][provider_id] = key
    save_config(cfg)


def get_stt_api_key(provider_id: str) -> str | None:
    return _get_provider_api_key("stt_api_keys", provider_id)


def set_stt_api_key(provider_id: str, key: str) -> None:
    _set_provider_api_key("stt_api_keys", provider_id, key)


def get_stt_provider() -> str:
    return str(load_config().get("stt_provider", "soniox"))


def set_stt_provider(provider_id: str) -> None:
    cfg = load_config()
    cfg["stt_provider"] = provider_id
    save_config(cfg)


def get_translation_api_key(provider_id: str) -> str | None:
    return _get_provider_api_key("translation_api_keys", provider_id)


def set_translation_api_key(provider_id: str, key: str) -> None:
    _set_provider_api_key("translation_api_keys", provider_id, key)


def get_translation_provider() -> str:
    return str(load_config().get("translation_provider", "soniox"))


def set_translation_provider(provider_id: str) -> None:
    cfg = load_config()
    cfg["translation_provider"] = provider_id
    save_config(cfg)


def get_translation_style() -> str:
    return str(load_config().get("translation_style", "natural"))


def set_translation_style(style_id: str) -> None:
    cfg = load_config()
    cfg["translation_style"] = style_id
    save_config(cfg)
