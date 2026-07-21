import asyncio
import pytest

from app.tts_provider import tts_cache
from app.tts_session import TtsSessionController

pytestmark = pytest.mark.asyncio


class RecordingBrowser:
    def __init__(self):
        self.json_messages = []
        self.audio = []

    async def send_json(self, payload):
        self.json_messages.append(payload)

    async def send_bytes(self, payload):
        self.audio.append(payload)


class ImmediateProvider:
    def __init__(self):
        self.calls = []

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
