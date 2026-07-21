"""Tests for TTS barge-in epoch logic + queue drain + cancel.

These test the pure-ish logic without real WebSockets — we use a fake TTS
WebSocket that records sent messages.
"""
import asyncio
import json

import pytest

from app.tts import (
    TTS_END,
    TTS_NONE,
    TTS_TEXT,
    TTS_BARGE,
    new_tts_state,
    trigger_barge,
    _drain_queue,
    _cancel_open_streams,
)


class FakeTtsWs:
    """Records every send() call as a parsed JSON dict."""
    def __init__(self):
        self.sent: list[dict] = []
        self._closed = False

    async def send(self, raw: str):
        if isinstance(raw, bytes):
            raw = raw.decode()
        self.sent.append(json.loads(raw))

    async def close(self):
        self._closed = True


class TestNewTtsState:
    def test_one_direction(self):
        state = new_tts_state(["es"])
        assert "es" in state["directions"]
        assert state["directions"]["es"]["current_stream_id"] is None
        assert state["directions"]["es"]["stream_used"] is False
        assert state["stt_done"] is False
        assert state["barge_epoch"] == 0

    def test_two_directions(self):
        state = new_tts_state(["en", "es"])
        assert set(state["directions"]) == {"en", "es"}
        # Each direction gets its own idle_event
        assert state["directions"]["en"]["idle_event"] is not state["directions"]["es"]["idle_event"]


class TestDrainQueue:
    async def test_drain_empties_queue(self):
        q: asyncio.Queue = asyncio.Queue()
        for i in range(5):
            await q.put(("text", f"item-{i}", "es"))
        assert q.qsize() == 5
        await _drain_queue(q)
        assert q.qsize() == 0

    async def test_drain_empty_queue_is_noop(self):
        q: asyncio.Queue = asyncio.Queue()
        await _drain_queue(q)
        assert q.qsize() == 0


class TestCancelOpenStreams:
    async def test_cancel_sends_cancel_for_each_open_stream(self):
        state = new_tts_state(["en", "es"])
        state["directions"]["en"]["current_stream_id"] = "utterance-1-en"
        state["directions"]["es"]["current_stream_id"] = "utterance-2-es"
        state["stream_id_to_direction"] = {
            "utterance-1-en": {"direction": "en", "line_id": 1},
            "utterance-2-es": {"direction": "es", "line_id": 2},
        }
        ws = FakeTtsWs()

        await _cancel_open_streams(ws, state)

        # Two cancel messages sent
        cancels = [m for m in ws.sent if m.get("cancel") is True]
        assert len(cancels) == 2
        cancel_ids = {m["stream_id"] for m in cancels}
        assert cancel_ids == {"utterance-1-en", "utterance-2-es"}

        # State cleared
        assert state["directions"]["en"]["current_stream_id"] is None
        assert state["directions"]["es"]["current_stream_id"] is None
        assert state["stream_id_to_direction"] == {}

    async def test_cancel_no_open_streams_is_noop(self):
        state = new_tts_state(["en"])
        ws = FakeTtsWs()
        await _cancel_open_streams(ws, state)
        assert ws.sent == []


class TestTriggerBarge:
    async def test_barge_increments_epoch_and_enqueues(self):
        state = new_tts_state(["es"])
        q: asyncio.Queue = asyncio.Queue()

        assert state["barge_epoch"] == 0
        await trigger_barge(q, state)
        assert state["barge_epoch"] == 1

        # Sentinel in queue
        item = q.get_nowait()
        assert item[0] == TTS_BARGE
        assert item[1] == 1  # epoch

    async def test_multiple_barges_increment_epoch(self):
        state = new_tts_state(["es"])
        q: asyncio.Queue = asyncio.Queue()

        await trigger_barge(q, state)
        await trigger_barge(q, state)
        await trigger_barge(q, state)
        assert state["barge_epoch"] == 3
        assert q.qsize() == 3
