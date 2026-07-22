"""Reconnect recovery tests: backoff, keepalive, buffering, and continuity."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from app import main, stt
from app.main import (
    RECONNECT_EXHAUSTED_CLOSE_CODE,
    RECONNECT_MAX_RETRIES,
    _buffer_audio_during_reconnect,
    _connection_close_details,
    _reconnect_delay,
    _websocket_close_details,
)
from app.stt import handle_stt, stt_keepalive
from app.tts import new_tts_state


class QueueBrowser:
    def __init__(self, messages: list[dict]):
        self.messages = list(messages)

    async def receive(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(1)


class RecordingBrowser:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class FinishedStt:
    async def recv(self) -> str:
        return json.dumps({"tokens": [], "finished": True})


class FailedStt:
    async def recv(self) -> str:
        raise ConnectionResetError("upstream dropped")


class BlockingKeepaliveSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.sent_event = asyncio.Event()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))
        self.sent_event.set()
        await asyncio.Future()


class FakeCloseError(Exception):
    code = 1011
    reason = "keepalive ping timeout"


class FakeClosedSocket:
    close_code = 1012
    close_reason = "service restart"


def test_reconnect_policy_is_bounded_exponential_backoff_with_jitter():
    assert RECONNECT_MAX_RETRIES == 5
    assert RECONNECT_EXHAUSTED_CLOSE_CODE == 4000
    assert _reconnect_delay(1, random_value=0.0) == pytest.approx(0.5)
    assert _reconnect_delay(2, random_value=0.0) == pytest.approx(1.0)
    assert _reconnect_delay(3, random_value=0.5) == pytest.approx(2.2)
    assert _reconnect_delay(99, random_value=1.0) == pytest.approx(10.0)


def test_connection_close_details_preserve_actual_code_and_reason():
    group = ExceptionGroup("session", [RuntimeError("other task"), FakeCloseError("closed")])
    assert _connection_close_details(group) == (1011, "keepalive ping timeout")
    assert _websocket_close_details(FakeClosedSocket()) == (1012, "service restart")


async def test_audio_buffer_keeps_newest_bytes_and_reports_overflow():
    controls: list[dict] = []

    async def on_text(data: dict) -> None:
        controls.append(data)

    browser = QueueBrowser(
        [
            {"bytes": b"abcd"},
            {"text": json.dumps({"type": "utterances", "utterances": [{"original": "kept"}]})},
            {"bytes": b"efgh"},
        ]
    )
    audio_buffer = bytearray()

    dropped = await _buffer_audio_during_reconnect(
        browser,
        audio_buffer,
        max_bytes=6,
        max_wait=0.02,
        on_text=on_text,
    )

    assert audio_buffer == b"cdefgh"
    assert dropped == 2
    assert controls == [{"type": "utterances", "utterances": [{"original": "kept"}]}]


async def test_stt_keepalive_uses_soniox_stt_control_message(monkeypatch):
    socket = BlockingKeepaliveSocket()
    monkeypatch.setattr(stt, "STT_KEEPALIVE_INTERVAL", 0)
    task = asyncio.create_task(stt_keepalive(socket))

    await asyncio.wait_for(socket.sent_event.wait(), timeout=0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert socket.sent == [{"type": "keepalive"}]


async def test_transient_stt_exit_does_not_finalize_shared_tts_queue():
    queue: asyncio.Queue = asyncio.Queue()
    state = new_tts_state(["vi"])
    browser = RecordingBrowser()

    with pytest.raises(ConnectionResetError, match="upstream dropped"):
        await handle_stt(
            stt_ws=FailedStt(),
            browser_ws=browser,
            tts_queue=queue,
            tts_state=state,
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
            finalize_session_on_exit=False,
        )

    assert queue.empty()
    assert state["stt_done"] is False
    assert browser.sent == []


async def test_clean_stt_finish_still_finalizes_session_during_reconnect_loop():
    queue: asyncio.Queue = asyncio.Queue()
    state = new_tts_state(["vi"])
    browser = RecordingBrowser()
    finished_event = asyncio.Event()

    await handle_stt(
        stt_ws=FinishedStt(),
        browser_ws=browser,
        tts_queue=queue,
        tts_state=state,
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
        finalize_session_on_exit=False,
        finished_event=finished_event,
    )

    assert finished_event.is_set()
    assert state["stt_done"] is True
    assert queue.qsize() == 2
    assert browser.sent == [
        {"tokens": [], "finished": True},
        {"session_done": True},
    ]


class FakeSession:
    id = "reconnect-session"

    def __init__(self):
        self.utterances: list[dict] = []
        self.closed = False

    def add_many(self, utterances: list[dict]) -> None:
        self.utterances.extend(utterances)

    def close(self) -> None:
        self.closed = True


class FakeTranscriptStore:
    def __init__(self, session: FakeSession):
        self.session = session
        self.finished: list[FakeSession] = []

    def new(self) -> FakeSession:
        return self.session

    def finish(self, session: FakeSession) -> None:
        self.finished.append(session)


class RecoveringBrowser:
    def __init__(self):
        self.receive_count = 0
        self.sent_json: list[dict] = []

    async def accept(self) -> None:
        pass

    async def receive(self) -> dict:
        self.receive_count += 1
        if self.receive_count == 1:
            return {
                "text": json.dumps(
                    {"type": "utterances", "utterances": [{"original": "before disconnect"}]}
                )
            }
        if self.receive_count == 2:
            return {"bytes": b"audio-before-drop"}
        await asyncio.sleep(0.02)
        raise WebSocketDisconnect(code=1000, reason="test complete")

    async def send_json(self, data: dict) -> None:
        self.sent_json.append(data)

    async def send_bytes(self, data: bytes) -> None:
        pass

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        pass


class DroppingSttSocket:
    close_code = 1011
    close_reason = "simulated upstream disconnect"

    async def send(self, data) -> None:
        if isinstance(data, bytes):
            raise FakeCloseError("simulated upstream disconnect")

    async def recv(self) -> str:
        await asyncio.Future()

    async def close(self) -> None:
        pass


class RecoveredSttSocket:
    close_code = None
    close_reason = None

    def __init__(self):
        self.responses = [
            json.dumps(
                {
                    "tokens": [
                        {"text": "after reconnect", "is_final": True, "language": "en"}
                    ]
                }
            )
        ]

    async def send(self, data) -> None:
        pass

    async def recv(self) -> str:
        if self.responses:
            return self.responses.pop(0)
        await asyncio.Future()

    async def close(self) -> None:
        pass


class DisconnectingBrowser:
    def __init__(self):
        self.sent_json: list[dict] = []

    async def accept(self) -> None:
        pass

    async def receive(self) -> dict:
        raise WebSocketDisconnect(code=1000, reason="config captured")

    async def send_json(self, data: dict) -> None:
        self.sent_json.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        pass


class ConfigCapturingSttSocket:
    close_code = None
    close_reason = None

    def __init__(self):
        self.configs: list[dict] = []

    async def send(self, data) -> None:
        if isinstance(data, str):
            self.configs.append(json.loads(data))

    async def recv(self) -> str:
        await asyncio.Future()

    async def close(self) -> None:
        pass


async def test_translation_websocket_rejects_style_unsupported_by_engine(monkeypatch):
    browser = DisconnectingBrowser()
    monkeypatch.setattr(main, "is_configured", lambda: True)

    await main.translation_websocket(
        browser,
        target_lang="vi",
        tts=False,
        translation_provider="soniox",
        translation_style="technical",
    )

    assert browser.sent_json[-1]["error_code"] == "unsupported_translation_style"


@pytest.mark.parametrize(
    ("requested_delay_ms", "expected_delay_ms"),
    [(100, 500), (2500, 2500), (5000, 3000)],
)
async def test_translation_websocket_clamps_endpoint_delay(
    monkeypatch, requested_delay_ms, expected_delay_ms
):
    browser = DisconnectingBrowser()
    socket = ConfigCapturingSttSocket()
    session = FakeSession()

    monkeypatch.setattr(main, "is_configured", lambda: True)
    monkeypatch.setattr(main, "transcript_store", FakeTranscriptStore(session))
    monkeypatch.setattr(main.websockets, "connect", AsyncMock(return_value=socket))
    monkeypatch.setattr(main, "create_conversation", AsyncMock())
    monkeypatch.setattr(main, "add_connection_event", AsyncMock())
    monkeypatch.setattr(main, "update_conversation", AsyncMock())
    monkeypatch.setattr(main, "add_segments_batch", AsyncMock(return_value=1))

    await main.translation_websocket(
        browser,
        target_lang="vi",
        tts=False,
        stt_delay_ms=requested_delay_ms,
    )

    assert socket.configs[0]["max_endpoint_delay_ms"] == expected_delay_ms


async def test_unexpected_stt_close_recovers_without_losing_transcript(monkeypatch):
    browser = RecoveringBrowser()
    session = FakeSession()
    store = FakeTranscriptStore(session)
    connect = AsyncMock(side_effect=[DroppingSttSocket(), RecoveredSttSocket()])

    monkeypatch.setattr(main, "is_configured", lambda: True)
    monkeypatch.setattr(main, "transcript_store", store)
    monkeypatch.setattr(main.websockets, "connect", connect)
    monkeypatch.setattr(main, "_reconnect_delay", lambda attempt: 0.0)
    monkeypatch.setattr(main, "create_conversation", AsyncMock())
    monkeypatch.setattr(main, "add_connection_event", AsyncMock())
    monkeypatch.setattr(main, "update_conversation", AsyncMock())
    monkeypatch.setattr(main, "add_segments_batch", AsyncMock(return_value=1))

    await main.translation_websocket(browser, target_lang="vi", tts=False)

    assert connect.await_count == 2
    assert session.utterances == [{"original": "before disconnect"}]
    assert session.closed is True
    assert store.finished == [session]
    assert any(message.get("reconnecting") for message in browser.sent_json)
    assert any(message.get("reconnected") for message in browser.sent_json)
    assert any(
        message.get("tokens", [{}])[0].get("text") == "after reconnect"
        for message in browser.sent_json
        if message.get("tokens")
    )
    assert not any(message.get("session_done") for message in browser.sent_json)
