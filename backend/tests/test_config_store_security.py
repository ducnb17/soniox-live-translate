import json
import sys
import types

import pytest

from app import config_store


@pytest.fixture
def fake_dpapi(tmp_path, monkeypatch):
    class FakeWin32Crypt:
        @staticmethod
        def CryptProtectData(data, *_args):
            return b"encrypted-for-current-user:" + data[::-1]

        @staticmethod
        def CryptUnprotectData(data, *_args):
            prefix = b"encrypted-for-current-user:"
            if not data.startswith(prefix):
                raise ValueError("wrong Windows user")
            return "SonioxLiveTranslate", data[len(prefix):][::-1]

    monkeypatch.setattr(config_store.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32crypt", FakeWin32Crypt)
    monkeypatch.setattr(config_store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_store, "config_path", lambda: tmp_path / "config.json")
    return tmp_path / "config.json"


def test_save_encrypts_keys_for_all_provider_domains(fake_dpapi):
    secrets = {
        "soniox_api_key": "soniox-secret-readable",
        "tts_api_keys": {
            "google": "google-secret-readable",
            "openai": "openai-secret-readable",
            "azure": "azure-secret-readable",
            "elevenlabs": "eleven-secret-readable",
            "polly": "aws-id:aws-secret-readable",
            "deepgram": "deepgram-secret-readable",
        },
        "tts_provider": "openai",
        "stt_api_keys": {"deepgram": "stt-deepgram-secret"},
        "translation_api_keys": {"deepl": "translation-deepl-secret"},
    }

    config_store.save_config(secrets)

    raw_text = fake_dpapi.read_text(encoding="utf-8")
    for plaintext in (
        "soniox-secret-readable",
        "google-secret-readable",
        "openai-secret-readable",
        "azure-secret-readable",
        "eleven-secret-readable",
        "aws-secret-readable",
        "deepgram-secret-readable",
        "stt-deepgram-secret",
        "translation-deepl-secret",
    ):
        assert plaintext not in raw_text
    raw = json.loads(raw_text)
    assert raw["soniox_api_key"].startswith(config_store.DPAPI_PREFIX)
    assert all(value.startswith(config_store.DPAPI_PREFIX) for value in raw["tts_api_keys"].values())
    assert all(value.startswith(config_store.DPAPI_PREFIX) for value in raw["stt_api_keys"].values())
    assert all(value.startswith(config_store.DPAPI_PREFIX) for value in raw["translation_api_keys"].values())
    assert config_store.load_config() == secrets


def test_plaintext_config_is_migrated_without_losing_values(fake_dpapi):
    legacy = {
        "soniox_api_key": "existing-soniox-key",
        "tts_api_keys": {"openai": "existing-openai-key"},
        "tts_provider": "openai",
        "tts_voices": {"openai": "nova"},
        "host": "127.0.0.1",
    }
    fake_dpapi.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = config_store.load_config()

    assert loaded == legacy
    migrated_text = fake_dpapi.read_text(encoding="utf-8")
    assert "existing-soniox-key" not in migrated_text
    assert "existing-openai-key" not in migrated_text
    assert json.loads(migrated_text)["tts_voices"] == {"openai": "nova"}


def test_failed_migration_keeps_original_plaintext_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = '{"soniox_api_key":"must-not-be-lost","port":8765}'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config_store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_store, "config_path", lambda: path)
    monkeypatch.setattr(config_store.sys, "platform", "win32")
    broken = types.SimpleNamespace(
        CryptProtectData=lambda *_args: (_ for _ in ()).throw(RuntimeError("DPAPI unavailable")),
    )
    monkeypatch.setitem(sys.modules, "win32crypt", broken)

    with pytest.raises(config_store.SecretProtectionError):
        config_store.load_config()

    assert path.read_text(encoding="utf-8") == original
