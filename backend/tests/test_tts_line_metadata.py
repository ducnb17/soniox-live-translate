import asyncio
import base64
import json

from app.stt import TTS_END, TTS_NONE, TTS_TEXT
from app.tts import new_tts_state, pipe_tts_to_browser, prewarm_stream, tts_sender


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
    prewarm_meta = state["stream_id_to_direction"]["prewarm-1-vi"]
    assert prewarm_meta["direction"] == "vi"
    assert prewarm_meta["line_id"] is None
    assert prewarm_meta["is_prewarm"] is True

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put((TTS_TEXT, "xin chào", "vi", 23))
    await queue.put((TTS_END, "vi"))
    await queue.put(TTS_NONE)
    await tts_sender(queue, state, tts_ws, {"vi": "Maya"})

    assert state["stream_id_to_direction"]["prewarm-1-vi"]["line_id"] == 23


async def test_stale_prewarm_is_rotated_before_first_text():
    state = new_tts_state(["vi"])
    tts_ws = RecordingTtsSenderWs()
    await prewarm_stream(tts_ws, state, "vi", "Maya")
    state["stream_id_to_direction"]["prewarm-1-vi"]["opened_at"] = 0.0

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put((TTS_TEXT, "xin chao", "vi", 24))
    await queue.put((TTS_END, "vi"))
    await queue.put(TTS_NONE)
    await tts_sender(queue, state, tts_ws, {"vi": "Maya"})

    assert tts_ws.messages[1] == {
        "stream_id": "prewarm-1-vi",
        "cancel": True,
    }
    assert tts_ws.messages[2]["stream_id"] == "utterance-1-vi"
    assert tts_ws.messages[3] == {
        "stream_id": "utterance-1-vi",
        "text": "xin chao",
        "text_end": False,
    }


async def test_audio_meta_immediately_precedes_binary_and_routes_terminated_stream():
    first_audio = b"pcm-audio-1"
    final_audio = b"pcm-audio-2"
    stream_id = "utterance-1-vi"
    state = new_tts_state(["vi"])
    state["directions"]["vi"]["current_stream_id"] = stream_id
    state["directions"]["vi"]["stream_used"] = True
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
                "audio_end": True,
            },
            {"stream_id": stream_id, "terminated": True},
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
    assert state["directions"]["vi"]["current_stream_id"] is None
    assert state["directions"]["vi"]["idle_event"].is_set()


async def test_terminated_without_audio_end_emits_zero_byte_end_marker():
    stream_id = "utterance-2-vi"
    state = new_tts_state(["vi"])
    state["stream_id_to_direction"][stream_id] = {
        "direction": "vi",
        "line_id": 18,
    }
    browser = RecordingBrowser()

    await pipe_tts_to_browser(
        FakeTtsWs([{"stream_id": stream_id, "terminated": True}]),
        browser,
        state,
    )

    assert browser.messages == [
        (
            "json",
            {
                "type": "audio_chunk_meta",
                "line_id": 18,
                "byte_length": 0,
                "line_audio_end": True,
            },
        ),
        ("bytes", b""),
    ]


async def test_expired_prewarm_is_silent_and_releases_direction():
    stream_id = "prewarm-1-vi"
    state = new_tts_state(["vi"])
    state["directions"]["vi"]["current_stream_id"] = stream_id
    state["stream_id_to_direction"][stream_id] = {
        "direction": "vi",
        "line_id": None,
    }
    browser = RecordingBrowser()

    await pipe_tts_to_browser(
        FakeTtsWs([
            {
                "stream_id": stream_id,
                "error_code": 408,
                "error_message": "Request timeout",
            }
        ]),
        browser,
        state,
    )

    assert browser.messages == []
    assert state["directions"]["vi"]["current_stream_id"] is None
    assert state["directions"]["vi"]["idle_event"].is_set()
