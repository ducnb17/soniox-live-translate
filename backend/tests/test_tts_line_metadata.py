import asyncio
import base64
import json

from app.tts import (
    TTS_END,
    TTS_NONE,
    TTS_TEXT,
    new_tts_state,
    pipe_tts_to_browser,
    prewarm_stream,
    tts_sender,
)


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

    assert state["stream_id_to_direction"]["prewarm-vi"] == {
        "direction": "vi",
        "line_id": 23,
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
    assert state["directions"]["vi"]["current_stream_id"] is None
    assert state["directions"]["vi"]["idle_event"].is_set()
