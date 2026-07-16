"""API smoke tests via FastAPI TestClient.

These hit the REST endpoints (health, config, setup, setup/status) without
needing a real Soniox connection. The WebSocket endpoint requires a real
Soniox backend so we test only the not-configured guard.
"""
import os

import pytest
from fastapi.testclient import TestClient

# Set a dummy key so app imports cleanly.
os.environ.setdefault("SONIOX_API_KEY", "test-key-for-api-tests")

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


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
    def test_returns_html(self, client):
        r = client.get("/setup")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")


class TestSetupPost:
    def test_rejects_empty_key(self, client):
        r = client.post("/setup", json={"soniox_api_key": ""})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_accepts_valid_key(self, client, tmp_path, monkeypatch):
        # Redirect config_store to a temp dir so we don't clobber real config.
        from app import config_store
        monkeypatch.setattr(config_store, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(config_store, "config_path", lambda: tmp_path / "config.json")

        r = client.post("/setup", json={"soniox_api_key": "new-test-key-xyz"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["configured"] is True

        # File written
        import json
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["soniox_api_key"] == "new-test-key-xyz"
