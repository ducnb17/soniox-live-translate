"""Tests for TranscriptStore + TranscriptSession lifecycle."""
import json
import time

import pytest

from app.transcript import TranscriptStore, TranscriptSession


class TestTranscriptSession:
    def test_new_has_uuid_id(self):
        s = TranscriptSession()
        assert len(s.id) == 32  # uuid4 hex
        assert s.utterances == []
        assert s.meta["started_at"] > 0
        assert s.meta["ended_at"] is None

    def test_add_and_add_many(self):
        s = TranscriptSession()
        s.add({"original": "hello"})
        s.add_many([{"original": "world"}, {"original": "!"}])
        assert len(s.utterances) == 3
        assert s.utterances[0]["original"] == "hello"
        assert s.utterances[2]["original"] == "!"

    def test_close_sets_ended_at(self):
        s = TranscriptSession()
        assert s.meta["ended_at"] is None
        s.close()
        assert s.meta["ended_at"] is not None
        assert s.meta["ended_at"] >= s.meta["started_at"]

    def test_payload_shape(self):
        s = TranscriptSession(id="abc123", utterances=[{"o": "hi"}])
        s.close()
        p = s.payload()
        assert p["session_id"] == "abc123"
        assert p["utterances"] == [{"o": "hi"}]
        assert "started_at" in p["meta"]
        assert "ended_at" in p["meta"]


class TestTranscriptStore:
    def test_new_and_get(self):
        store = TranscriptStore()
        s = store.new()
        assert s.id in store.sessions
        assert store.get(s.id) is s

    def test_get_unknown_404(self):
        store = TranscriptStore()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            store.get("nonexistent-id-123")
        assert exc.value.status_code == 404

    def test_finish_persists_and_removes(self, tmp_path, monkeypatch):
        from app import transcript as tmod
        monkeypatch.setattr(tmod, "TRANSCRIPT_DIR", tmp_path)

        store = TranscriptStore()
        s = store.new()
        s.add({"original": "test utterance"})
        s.close()
        store.finish(s)

        # Removed from live registry
        assert s.id not in store.sessions

        # File written
        path = tmp_path / f"{s.id}.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["session_id"] == s.id
        assert data["utterances"] == [{"original": "test utterance"}]

    def test_get_loads_from_disk(self, tmp_path, monkeypatch):
        from app import transcript as tmod
        monkeypatch.setattr(tmod, "TRANSCRIPT_DIR", tmp_path)

        # Write a transcript file directly
        sid = "loaded-from-disk"
        path = tmp_path / f"{sid}.json"
        path.write_text(json.dumps({
            "session_id": sid,
            "meta": {"started_at": 1, "ended_at": 2},
            "utterances": [{"o": "restored"}],
        }))

        store = TranscriptStore()
        s = store.get(sid)
        assert s.id == sid
        assert s.utterances == [{"o": "restored"}]
