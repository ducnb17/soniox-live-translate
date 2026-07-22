import asyncio
import base64
import json
from types import SimpleNamespace
import pytest

from app.tts_provider import tts_cache
from app.tts_session import TtsSessionController

pytestmark = pytest.mark.asyncio


class RecordingBrowser:
    def __init__(self):
        self.json_messages = []
        self.audio = []
        self.audio_event = asyncio.Event()

    async def send_json(self, payload):
        self.json_messages.append(payload)

    async def send_bytes(self, payload):
        self.audio.append(payload)
        self.audio_event.set()


class FakeSonioxSocket:
    def __init__(self):
        self.sent = []
        self.responses = asyncio.Queue()
        self.closed = False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        return await self.responses.get()

    async def close(self):
        self.closed = True


class ImmediateProvider:
    def __init__(self):
        self.calls = []

    @property
    def info(self):
        # REST-backed providers yield one completed audio response.  They are
        # not true streaming providers, but remain valid /ws/tts providers.
        return SimpleNamespace(supports_streaming=False)

    async def synthesize_stream(self, text, voice_id, lang):
        self.calls.append((text, voice_id, lang))
        yield b"first"
        yield b"last"

    def estimate_cost(self, char_count):
        return char_count / 1_000_000


class BlockingProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.finalized = asyncio.Event()

    async def synthesize_stream(self, text, voice_id, lang):
        del text, voice_id, lang
        self.started.set()
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            self.finalized.set()


class FailingProvider:
    def __init__(self, message="provider quota exhausted"):
        self.message = message

    async def synthesize_stream(self, text, voice_id, lang):
        del text, voice_id, lang
        raise RuntimeError(self.message)
        yield b"unreachable"


async def start_controller(provider):
    tts_cache.clear()
    browser = RecordingBrowser()

    async def unavailable_fallback(_text, _voice, _lang):
        raise RuntimeError("Soniox fallback unavailable")

    controller = TtsSessionController(
        browser,
        lambda _pid, _key: provider,
        fallback_synthesizer=unavailable_fallback,
    )
    controller._worker = asyncio.create_task(controller._worker_loop())
    await controller.handle_command({
        "type": "configure",
        "enabled": True,
        "provider": "fake",
        "voice": "voice-a",
        "epoch": 0,
    })
    return browser, controller


async def test_speak_is_deduplicated_and_audio_metadata_carries_epoch():
    provider = ImmediateProvider()
    browser, controller = await start_controller(provider)
    command = {
        "type": "speak",
        "request_id": "session:7",
        "line_id": 7,
        "text": "xin chao",
        "lang": "vi",
        "epoch": 0,
    }
    await controller.handle_command(command)
    await controller.handle_command(command)
    await asyncio.wait_for(controller.queue.join(), 1)

    assert provider.calls == [("xin chao", "voice-a", "vi")]
    assert browser.audio == [b"firstlast"]
    metas = [m for m in browser.json_messages if m.get("type") == "audio_chunk_meta"]
    assert [m["line_audio_end"] for m in metas] == [True]
    assert {m["epoch"] for m in metas} == {0}
    assert {m["request_id"] for m in metas} == {"session:7"}
    await controller.close()


async def test_non_streaming_provider_is_accepted_by_independent_session():
    provider = ImmediateProvider()
    browser, controller = await start_controller(provider)

    assert controller.enabled is True
    assert browser.json_messages[-1]["type"] == "tts_state"
    assert browser.json_messages[-1]["state"] == "on"
    assert not any(m.get("type") == "tts_error" for m in browser.json_messages)
    await controller.close()


async def test_cancel_all_awaits_active_synthesis_and_drops_stale_audio():
    provider = BlockingProvider()
    browser, controller = await start_controller(provider)
    await controller.handle_command({
        "type": "speak",
        "request_id": "session:8",
        "line_id": 8,
        "text": "cancel me",
        "lang": "en",
        "epoch": 0,
    })
    await asyncio.wait_for(provider.started.wait(), 1)
    await controller.handle_command({"type": "cancel_all", "epoch": 1})

    assert provider.finalized.is_set()
    assert controller.epoch == 1
    assert controller.enabled is True
    assert browser.audio == []
    assert browser.json_messages[-1]["state"] == "on"
    assert controller._worker is not None and not controller._worker.done()
    await controller.close()


@pytest.mark.parametrize("provider_error", ["401 unauthorized", "429 quota", "500 upstream", "timeout"])
async def test_provider_failure_is_reported_without_killing_worker(provider_error):
    browser, controller = await start_controller(FailingProvider(provider_error))
    await controller.handle_command({
        "type": "speak",
        "request_id": "session:9",
        "line_id": 9,
        "text": "fail",
        "lang": "en",
        "epoch": 0,
    })
    await asyncio.wait_for(controller.queue.join(), 1)
    errors = [m for m in browser.json_messages if m.get("type") == "tts_error"]
    fallbacks = [m for m in browser.json_messages if m.get("type") == "tts_fallback"]
    assert errors[0]["request_id"] == "session:9"
    assert errors[0]["epoch"] == 0
    assert provider_error in fallbacks[0]["reason"]
    assert "fallback unavailable" in errors[0]["message"]
    assert errors[0]["recoverable"] is True
    assert controller._worker is not None and not controller._worker.done()
    await controller.close()


async def test_soniox_builtin_uses_cancellable_websocket_synthesizer():
    calls = []

    async def synthesize(text, voice, lang):
        calls.append((text, voice, lang))
        return b"soniox-pcm"

    tts_cache.clear()
    browser = RecordingBrowser()
    controller = TtsSessionController(browser, fallback_synthesizer=synthesize)
    controller._worker = asyncio.create_task(controller._worker_loop())
    await controller.handle_command({
        "type": "configure", "enabled": True, "provider": "soniox",
        "voice": "Maya", "epoch": 0,
    })
    await controller.handle_command({
        "type": "speak", "request_id": "s:10", "line_id": 10,
        "text": "hello", "direction": "en", "epoch": 0,
    })
    await asyncio.wait_for(controller.queue.join(), 1)

    assert calls == [("hello", "Maya", "en")]
    assert browser.audio == [b"soniox-pcm"]
    assert any(m.get("type") == "line_audio_complete" for m in browser.json_messages)
    await controller.close()


async def test_soniox_realtime_stream_forwards_text_and_audio_incrementally():
    tts_cache.clear()
    browser = RecordingBrowser()
    soniox = FakeSonioxSocket()

    async def connect(*_args, **_kwargs):
        return soniox

    controller = TtsSessionController(
        browser,
        provider_factory=lambda _pid, _key: ImmediateProvider(),
        realtime_connector=connect,
    )
    controller._worker = asyncio.create_task(controller._worker_loop())
    await controller.handle_command({
        "type": "configure",
        "enabled": True,
        "provider": "soniox",
        "voice": "Maya",
        "mode": "one_way",
        "target_lang": "vi",
        "realtime_streaming": True,
        "epoch": 0,
    })
    stream_id = soniox.sent[0]["stream_id"]
    assert soniox.sent[0]["model"] == "tts-rt-v1"
    assert soniox.sent[0]["audio_format"] == "pcm_s16le"
    assert soniox.sent[0]["sample_rate"] == 24000

    first = {
        "type": "stream_text", "request_id": "s:rt:1", "line_id": 1,
        "text": "xin ", "direction": "vi", "voice": "Maya",
        "sequence": 1, "epoch": 0,
    }
    await controller.handle_command(first)
    await controller.handle_command(first)  # duplicate sequence is ignored
    await controller.handle_command({
        **first, "text": "chào", "sequence": 2,
    })
    await controller.handle_command({
        "type": "stream_end", "request_id": "s:rt:1", "epoch": 0,
    })

    text_frames = [message for message in soniox.sent if "text" in message]
    assert [(m["text"], m["text_end"]) for m in text_frames] == [
        ("xin ", False), ("chào", False), ("", True),
    ]
    await soniox.responses.put(json.dumps({
        "stream_id": stream_id,
        "audio": base64.b64encode(b"first").decode(),
        "audio_end": False,
    }))
    await soniox.responses.put(json.dumps({
        "stream_id": stream_id,
        "audio": base64.b64encode(b"last").decode(),
        "audio_end": True,
    }))
    await soniox.responses.put(json.dumps({"stream_id": stream_id, "terminated": True}))

    await asyncio.wait_for(browser.audio_event.wait(), 1)
    for _ in range(20):
        if len(browser.audio) == 2 and any(
            m.get("type") == "line_audio_complete" for m in browser.json_messages
        ):
            break
        await asyncio.sleep(0)
    assert browser.audio == [b"first", b"last"]
    metas = [m for m in browser.json_messages if m.get("type") == "audio_chunk_meta"]
    assert [m["line_audio_end"] for m in metas] == [False, True]
    usage = next(m for m in browser.json_messages if m.get("type") == "tts_usage")
    assert usage["characters"] == len("xin chào")
    await controller.close()


async def test_soniox_prewarm_error_sets_retryable_error_state():
    browser = RecordingBrowser()
    soniox = FakeSonioxSocket()

    async def connect(*_args, **_kwargs):
        return soniox

    controller = TtsSessionController(
        browser,
        provider_factory=lambda _pid, _key: ImmediateProvider(),
        realtime_connector=connect,
    )
    controller._worker = asyncio.create_task(controller._worker_loop())
    await controller.handle_command({
        "type": "configure", "enabled": True, "provider": "soniox",
        "voice": "Maya", "target_lang": "vi",
        "realtime_streaming": True, "epoch": 0,
    })
    await soniox.responses.put(json.dumps({
        "stream_id": soniox.sent[0]["stream_id"],
        "error_code": 401,
        "error_message": "invalid api key",
    }))
    for _ in range(20):
        if any(m.get("state") == "error" for m in browser.json_messages):
            break
        await asyncio.sleep(0)

    assert controller.enabled is False
    assert soniox.closed is True
    error = next(m for m in browser.json_messages if m.get("type") == "tts_error")
    assert "invalid api key" in error["message"]
    assert browser.json_messages[-1]["state"] == "error"
    await controller.close()
