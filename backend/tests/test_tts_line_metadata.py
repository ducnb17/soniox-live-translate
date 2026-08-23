import asyncio
import base64
import json

import pytest

from app.stt import TTS_END, TTS_NONE, TTS_TEXT
from app import config_store
from app import tts as tts_module
from app.tts import new_tts_state, pipe_tts_to_browser, prewarm_stream, tts_sender


@pytest.fixture(autouse=True)
def _no_dpapi(monkeypatch):
    """Avoid Windows DPAPI requirement when reading TTS keys on Linux."""
    monkeypatch.setattr(config_store, "get_tts_api_key", lambda provider_id: None)
    monkeypatch.setattr(tts_module, "get_tts_api_key", lambda provider_id: None)


class FakeTtsWs:
    def __init__(self, messages: list[dict]):
        self.messages = list(messages)

    async def recv(self) -> str:
        if not self.messages:
            raise RuntimeError("test complete")
        return json.dumps(self.messages.pop(0))


class RecordingBrowser:
    def __init__(self):
        self.messages: list[tuple[str, object]] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(("json", payload))

    async def send_bytes(self, payload: bytes) -> None:
        self.messages.append(("bytes", payload))


class RecordingTtsSenderWs:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


async def test_prewarmed_stream_is_bound_to_first_text_line_id():
    state = new_tts_state(["vi"])
    tts_ws = RecordingTtsSenderWs()
    await prewarm_stream(tts_ws, state, "vi", "Maya")
    assert state["stream_id_to_direction"]["prewarm-vi"] == {
        "direction": "vi",
        "line_id": None,
    }

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put((TTS_TEXT, "xin chào", "vi", 23))
    await queue.put((TTS_END, "vi"))
    await queue.put(TTS_NONE)
    await tts_sender(queue, state, tts_ws, {"vi": "Maya"})

    # Per-sentence streams: the first text opens a fresh `utt-1-vi` stream
    # and sends the full sentence with text_end=True. The prewarmed stream
    # stays unclaimed (TTS_END cancels it).
    assert state["stream_id_to_direction"]["utt-1-vi"] == {
        "direction": "vi",
        "line_id": 23,
    }
    sent_text = [m for m in tts_ws.messages if m.get("text")]
    assert sent_text and sent_text[0]["text"] == "xin chào"
    assert sent_text[0]["text_end"] is True
    # The prewarmed stream was cancelled on TTS_END.
    assert state["stream_id_to_direction"].get("prewarm-vi") is None


async def test_audio_meta_immediately_precedes_binary_and_routes_terminated_stream():
    first_audio = b"pcm-audio-1"
    final_audio = b"pcm-audio-2"
    stream_id = "utt-1-vi"
    state = new_tts_state(["vi"])
    state["directions"]["vi"]["streams"] = {stream_id: 17}
    state["stream_id_to_direction"][stream_id] = {
        "direction": "vi",
        "line_id": 17,
    }
    browser = RecordingBrowser()
    tts_ws = FakeTtsWs(
        [
            {
                "stream_id": stream_id,
                "audio": base64.b64encode(first_audio).decode(),
            },
            {
                "stream_id": stream_id,
                "audio": base64.b64encode(final_audio).decode(),
                "terminated": True,
            }
        ]
    )

    await pipe_tts_to_browser(tts_ws, browser, state)

    assert browser.messages == [
        (
            "json",
            {
                "type": "audio_chunk_meta",
                "line_id": 17,
                "byte_length": len(first_audio),
                "line_audio_end": False,
            },
        ),
        ("bytes", first_audio),
        (
            "json",
            {
                "type": "audio_chunk_meta",
                "line_id": 17,
                "byte_length": len(final_audio),
                "line_audio_end": True,
            },
        ),
        ("bytes", final_audio),
    ]
    assert stream_id not in state["stream_id_to_direction"]
    assert state["directions"]["vi"]["streams"] == {}
    assert state["directions"]["vi"]["prewarmed"] is None
