"""API smoke tests via FastAPI TestClient.

These hit the REST endpoints (health, config, setup, setup/status) without
needing a real Soniox connection. The WebSocket endpoint requires a real
Soniox backend so we test only the not-configured guard.
"""
import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

# Set a dummy key so app imports cleanly.
os.environ.setdefault("SONIOX_API_KEY", "test-key-for-api-tests")

from app.main import app
from app import main


@pytest.fixture(autouse=True)
def isolated_encrypted_config(tmp_path, monkeypatch):
    """Keep API tests away from the user's real config and emulate DPAPI."""
    from app import config_store

    monkeypatch.setattr(config_store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_store, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        config_store,
        "_protect_secret",
        lambda value: config_store.DPAPI_PREFIX + value.encode().hex(),
    )
    monkeypatch.setattr(
        config_store,
        "_unprotect_secret",
        lambda value: bytes.fromhex(value[len(config_store.DPAPI_PREFIX):]).decode(),
    )


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_returns_application_version(self, client):
        response = client.get("/api/version")
        assert response.status_code == 200
        assert response.json()["version"] == "0.3.5"


class TestConfig:
    def test_returns_voices_and_languages(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        data = r.json()
        assert "voices" in data
        assert "languages" in data
        assert len(data["voices"]) == 12
        assert len(data["languages"]) == 60
        assert "configured" in data

    def test_languages_have_code_and_name(self, client):
        r = client.get("/config")
        langs = r.json()["languages"]
        for item in langs:
            assert "code" in item
            assert "name" in item
            assert len(item["code"]) == 2


class TestSetupStatus:
    def test_returns_configured_bool(self, client):
        r = client.get("/setup/status")
        assert r.status_code == 200
        assert "configured" in r.json()


class TestSetupPage:
    def test_resolves_pyinstaller_frontend_from_bundle_root(
        self, monkeypatch, tmp_path
    ):
        bundle_root = tmp_path / "_internal"
        monkeypatch.setattr(main.sys, "frozen", True, raising=False)
        monkeypatch.setattr(main.sys, "_MEIPASS", str(bundle_root), raising=False)

        resolved = main._resolve_frontend_source_dir()

        assert resolved == str(bundle_root / "frontend")

    def test_returns_html(self, client):
        r = client.get("/setup")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_falls_back_to_source_when_frontend_dist_is_absent(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(main, "_static_dir", str(tmp_path / "missing-dist"))

        r = client.get("/setup")

        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")


class TestSetupPost:
    def test_rejects_empty_key(self, client):
        r = client.post("/setup", json={"soniox_api_key": ""})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_accepts_valid_key(self, client, tmp_path):
        from app import config_store

        r = client.post("/setup", json={"soniox_api_key": "new-test-key-xyz"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["configured"] is True

        # File written
        import json
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["soniox_api_key"].startswith(config_store.DPAPI_PREFIX)
        assert "new-test-key-xyz" not in (tmp_path / "config.json").read_text()
        assert config_store.get_api_key() == "new-test-key-xyz"


class TestTtsProviderApi:
    expected_providers = {
        "soniox", "google", "openai", "azure", "elevenlabs", "deepgram", "polly",
    }

    def test_lists_all_seven_provider_choices(self, client):
        response = client.get("/api/tts/providers")

        assert response.status_code == 200
        assert {provider["id"] for provider in response.json()} == self.expected_providers
        assert all(
            provider["tier"] in {"free", "cheap", "premium"}
            for provider in response.json()
        )

    @pytest.mark.parametrize("provider_id", sorted(expected_providers))
    def test_each_provider_voice_dropdown_has_options(self, client, provider_id):
        response = client.get(f"/api/tts/providers/{provider_id}/voices?lang=en")

        assert response.status_code == 200
        voices = response.json()
        assert voices
        assert all(voice["id"] and voice["name"] for voice in voices)


class SuccessfulConnectionProvider:
    async def test_connection(self):
        return True, "OK"


@pytest.mark.parametrize(
    ("domain", "getter_name", "key_setter_name", "provider_setter_name"),
    [
        ("tts", "get_tts_provider_instance", "set_tts_api_key", "set_tts_provider"),
        ("stt", "get_stt_provider_instance", "set_stt_api_key", "set_stt_provider"),
        (
            "translation", "get_translation_provider_instance",
            "set_translation_api_key", "set_translation_provider",
        ),
    ],
)
def test_successful_provider_test_saves_key_and_selects_provider(
    client, monkeypatch, domain, getter_name, key_setter_name, provider_setter_name,
):
    key_setter = Mock()
    provider_setter = Mock()
    monkeypatch.setattr(main, getter_name, lambda provider_id, api_key=None: SuccessfulConnectionProvider())
    monkeypatch.setattr(main, key_setter_name, key_setter)
    monkeypatch.setattr(main, provider_setter_name, provider_setter)

    response = client.post(f"/api/{domain}/providers/example/test", json={"api_key": "live-key"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "OK"}
    key_setter.assert_called_once_with("example", "live-key")
    provider_setter.assert_called_once_with("example")


class TestUnifiedConfigSave:
    """Tests for POST /api/config/save."""

    def test_saves_all_fields_when_provided(self, client, monkeypatch):
        tts_provider = Mock()
        tts_key = Mock()
        tts_voice = Mock()
        stt_provider = Mock()
        stt_key = Mock()
        translation_provider = Mock()
        translation_key = Mock()

        monkeypatch.setattr(main, "set_tts_provider", tts_provider)
        monkeypatch.setattr(main, "set_tts_api_key", tts_key)
        monkeypatch.setattr(main, "set_tts_voice", tts_voice)
        monkeypatch.setattr(main, "set_stt_provider", stt_provider)
        monkeypatch.setattr(main, "set_stt_api_key", stt_key)
        monkeypatch.setattr(main, "set_translation_provider", translation_provider)
        monkeypatch.setattr(main, "set_translation_api_key", translation_key)

        payload = {
            "tts_provider": "elevenlabs",
            "tts_voice": "Adam",
            "stt_provider": "soniox",
            "translation_provider": "google",
            "tts_api_key": "tk-abc",
            "stt_api_key": "sk-xyz",
            "translation_api_key": "tk-trans",
        }

        r = client.post("/api/config/save", json=payload)
        assert r.status_code == 200
        assert r.json() == {"ok": True}

        tts_provider.assert_called_once_with("elevenlabs")
        tts_key.assert_called_once_with("elevenlabs", "tk-abc")
        tts_voice.assert_called_once_with("elevenlabs", "Adam")
        stt_provider.assert_called_once_with("soniox")
        stt_key.assert_called_once_with("soniox", "sk-xyz")
        translation_provider.assert_called_once_with("google")
        translation_key.assert_called_once_with("google", "tk-trans")

    def test_empty_keys_dont_overwrite(self, client, monkeypatch):
        """Empty or missing key fields should not call the key setter."""
        tts_key = Mock()
        stt_key = Mock()
        translation_key = Mock()

        monkeypatch.setattr(main, "set_tts_provider", Mock())
        monkeypatch.setattr(main, "set_tts_api_key", tts_key)
        monkeypatch.setattr(main, "set_tts_voice", Mock())
        monkeypatch.setattr(main, "set_stt_provider", Mock())
        monkeypatch.setattr(main, "set_stt_api_key", stt_key)
        monkeypatch.setattr(main, "set_translation_provider", Mock())
        monkeypatch.setattr(main, "set_translation_api_key", translation_key)

        payload = {
            "tts_provider": "soniox",
            "tts_voice": "Maya",
            "stt_provider": "deepgram",
            "translation_provider": "soniox",
            # all keys omitted
        }

        r = client.post("/api/config/save", json=payload)
        assert r.status_code == 200
        assert r.json() == {"ok": True}

        tts_key.assert_not_called()
        stt_key.assert_not_called()
        translation_key.assert_not_called()

    def test_saves_with_missing_providers(self, client, monkeypatch):
        """Should not crash when some provider fields are missing."""
        monkeypatch.setattr(main, "set_tts_provider", Mock())
        monkeypatch.setattr(main, "set_tts_api_key", Mock())
        monkeypatch.setattr(main, "set_stt_provider", Mock())
        monkeypatch.setattr(main, "set_stt_api_key", Mock())
        monkeypatch.setattr(main, "set_translation_provider", Mock())
        monkeypatch.setattr(main, "set_translation_api_key", Mock())

        r = client.post("/api/config/save", json={"tts_voice": "Maya"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}


class TestTtsConfigApi:
    """Tests for GET /api/tts/config — returns TTS, STT, and translation config."""

    def test_returns_all_provider_config_fields(self, client, monkeypatch):
        monkeypatch.setattr(main, "get_tts_provider", lambda: "elevenlabs")
        monkeypatch.setattr(main, "get_tts_voice", lambda pid: "Adam")
        monkeypatch.setattr(main, "get_stt_provider", lambda: "google_v2")
        monkeypatch.setattr(main, "get_stt_api_key", lambda pid: "sk-long-key-abcde" if pid == "google_v2" else None)
        monkeypatch.setattr(main, "get_translation_provider", lambda: "soniox")
        monkeypatch.setattr(main, "get_translation_api_key", lambda pid: None if pid == "soniox" else None)

        r = client.get("/api/tts/config")
        assert r.status_code == 200
        data = r.json()

        assert data["current_provider"] == "elevenlabs"
        assert data["current_voice"] == "Adam"
        assert "configured_providers" in data
        assert data["selected_stt_provider"] == "google_v2"
        assert data["stt_api_key_present"] is True
        assert data["stt_api_key_masked"] == "sk-l****bcde"
        assert data["selected_translation_provider"] == "soniox"
        assert data["translation_api_key_present"] is False
        assert data["translation_api_key_masked"] == ""

    def test_no_keys_returns_false_and_empty_mask(self, client, monkeypatch):
        monkeypatch.setattr(main, "get_tts_provider", lambda: "soniox")
        monkeypatch.setattr(main, "get_tts_voice", lambda pid: "Maya")
        monkeypatch.setattr(main, "get_stt_provider", lambda: "soniox")
        monkeypatch.setattr(main, "get_stt_api_key", lambda pid: None)
        monkeypatch.setattr(main, "get_translation_provider", lambda: "soniox")
        monkeypatch.setattr(main, "get_translation_api_key", lambda pid: None)

        r = client.get("/api/tts/config")
        data = r.json()

        assert data["stt_api_key_present"] is False
        assert data["stt_api_key_masked"] == ""
        assert data["translation_api_key_present"] is False
        assert data["translation_api_key_masked"] == ""


class TestConversationApi:
    def test_list_and_search_forward_pagination(self, client, monkeypatch):
        list_mock = AsyncMock(return_value=[{"id": "listed"}])
        search_mock = AsyncMock(return_value=[{"id": "matched"}])
        monkeypatch.setattr(main, "list_conversations", list_mock)
        monkeypatch.setattr(main, "search_conversations", search_mock)

        listed = client.get("/api/conversations?limit=11&offset=20")
        searched = client.get("/api/conversations/search?q=hello%20world&limit=11&offset=10")

        assert listed.status_code == 200 and listed.json() == [{"id": "listed"}]
        assert searched.status_code == 200 and searched.json() == [{"id": "matched"}]
        list_mock.assert_awaited_once_with(limit=11, offset=20)
        search_mock.assert_awaited_once_with("hello world", limit=11, offset=10)

    @pytest.mark.parametrize(
        ("format_name", "content", "content_type"),
        [
            ("txt", "Original: Hello\nTranslated: Xin chào", "text/plain"),
            ("srt", "1\n00:00:00,000 --> 00:00:01,000\nHello\n", "application/x-subrip"),
            ("json", '{"id":"conv-export","segments":[]}', "application/json"),
        ],
    )
    def test_export_downloads_real_file(self, client, monkeypatch, format_name, content, content_type):
        monkeypatch.setattr(main, "get_conversation", AsyncMock(return_value={"id": "conv-export"}))
        monkeypatch.setattr(main, f"export_conversation_{format_name}", AsyncMock(return_value=content))

        response = client.get(f"/api/conversations/conv-export/export?format={format_name}")

        assert response.status_code == 200
        assert content_type in response.headers["content-type"]
        assert response.headers["content-disposition"] == (
            f'attachment; filename="conversation-conv-export.{format_name}"'
        )
        assert response.content.decode() == content

    def test_manual_cleanup_uses_selected_retention_days(self, client, monkeypatch):
        cleanup_mock = AsyncMock(return_value=4)
        monkeypatch.setattr(main, "cleanup_old_conversations", cleanup_mock)

        response = client.post("/api/retention/cleanup?max_age_days=45")

        assert response.status_code == 200
        assert response.json() == {"deleted": 4}
        cleanup_mock.assert_awaited_once_with(max_age_days=45)
