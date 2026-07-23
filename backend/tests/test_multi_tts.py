import asyncio

from app.external_tts import external_tts_sender
from app.stt import TTS_END, TTS_NONE, TTS_TEXT
from app.tts import new_tts_state
from app.tts_provider import get_available_providers, get_provider


class FakeBrowser:
    def __init__(self):
        self.json_messages = []
        self.audio = []

    async def send_json(self, payload):
        self.json_messages.append(payload)

    async def send_bytes(self, payload):
        self.audio.append(payload)


class SuccessfulProvider:
    def __init__(self):
        self.calls = 0

    async def synthesize_stream(self, text, voice_id, lang):
        self.calls += 1
        yield f"pcm:{voice_id}:{lang}:{text}".encode()

    def estimate_cost(self, char_count):
        return char_count * 0.000015


class StreamingProvider(SuccessfulProvider):
    async def synthesize_stream(self, text, voice_id, lang):
        self.calls += 1
        yield b"first-"
        yield b"second"


class InterruptedStreamingProvider(SuccessfulProvider):
    async def synthesize_stream(self, text, voice_id, lang):
        self.calls += 1
        yield b"first-"
        yield b"last-safe-chunk"
        raise RuntimeError("stream interrupted")


class FailingProvider:
    async def synthesize_stream(self, text, voice_id, lang):
        raise RuntimeError("quota exhausted")
        yield b""  # pragma: no cover - keeps this an async generator

    def estimate_cost(self, char_count):
        return 0.0


async def run_sender(provider, provider_id="openai", fallback=None):
    queue = asyncio.Queue()
    state = new_tts_state(["vi"])
    browser = FakeBrowser()
    await queue.put((TTS_TEXT, "xin chào", "vi", 1))
    await queue.put((TTS_END, "vi"))
    await queue.put(TTS_NONE)
    kwargs = {}
    if fallback is not None:
        kwargs["fallback_synthesize"] = fallback
    await external_tts_sender(
        tts_queue=queue,
        tts_state=state,
        browser_ws=browser,
        provider_id=provider_id,
        provider=provider,
        direction_voices={"vi": "nova"},
        **kwargs,
    )
    return browser


async def test_all_seven_providers_are_registered_and_have_voices():
    expected = {"soniox", "google", "openai", "azure", "elevenlabs", "deepgram", "polly"}
    infos = get_available_providers()
    assert {info.id for info in infos} == expected
    assert {info.tier for info in infos} <= {"free", "cheap", "premium"}
    assert next(info for info in infos if info.id == "elevenlabs").tier == "premium"
    for provider_id in expected:
        provider = get_provider(provider_id)
        assert provider is not None
        assert await provider.list_voices(lang="en")


async def test_external_provider_synthesizes_every_phrase_without_cache():
    provider = SuccessfulProvider()

    first = await run_sender(provider)
    second = await run_sender(provider)

    assert provider.calls == 2
    assert first.audio == [b"pcm:nova:vi:xin ch\xc3\xa0o"]
    assert first.json_messages[0] == {
        "type": "audio_chunk_meta",
        "line_id": 1,
        "byte_length": len(first.audio[0]),
        "line_audio_end": True,
    }
    first_usage = next(message["tts_usage"] for message in first.json_messages if "tts_usage" in message)
    second_usage = next(message["tts_usage"] for message in second.json_messages if "tts_usage" in message)
    assert first_usage["characters"] == len("xin chào")
    assert first_usage["estimated_cost_usd"] == len("xin chào") * 0.000015
    assert second_usage["estimated_cost_usd"] == len("xin chào") * 0.000015
    assert "cache_hit" not in first_usage
    assert "cache_hit" not in second_usage


async def test_provider_quota_error_notifies_user_and_falls_back_to_soniox():
    async def fallback(text, voice, lang):
        assert (text, voice, lang) == ("xin chào", "Maya", "vi")
        return b"soniox-fallback-pcm"

    browser = await run_sender(FailingProvider(), provider_id="openai", fallback=fallback)

    fallback_event = next(message["tts_fallback"] for message in browser.json_messages if "tts_fallback" in message)
    usage = next(message["tts_usage"] for message in browser.json_messages if "tts_usage" in message)
    assert fallback_event == {
        "from_provider": "openai",
        "to_provider": "soniox",
        "reason": "quota exhausted",
    }
    assert browser.audio == [b"soniox-fallback-pcm"]
    assert usage["provider_id"] == "soniox"
    assert usage["characters"] == len("xin chào")


async def test_streaming_provider_forwards_chunks_without_collecting_the_response():
    browser = await run_sender(StreamingProvider())

    metas = [message for message in browser.json_messages if message.get("type") == "audio_chunk_meta"]
    assert browser.audio == [b"first-", b"second"]
    assert [meta["byte_length"] for meta in metas] == [6, 6]
    assert [meta["line_audio_end"] for meta in metas] == [False, True]


async def test_interrupted_stream_closes_the_started_audio_line_without_replaying_it():
    fallback_calls = 0

    async def fallback(text, voice, lang):
        nonlocal fallback_calls
        fallback_calls += 1
        return b"duplicate-fallback"

    browser = await run_sender(InterruptedStreamingProvider(), fallback=fallback)

    metas = [message for message in browser.json_messages if message.get("type") == "audio_chunk_meta"]
    assert browser.audio == [b"first-", b"last-safe-chunk"]
    assert [meta["line_audio_end"] for meta in metas] == [False, True]
    assert fallback_calls == 0
    assert any(message.get("tts_error", {}).get("message") == "stream interrupted" for message in browser.json_messages)


async def test_external_provider_keeps_each_line_as_separate_labeled_audio():
    provider = SuccessfulProvider()
    queue = asyncio.Queue()
    state = new_tts_state(["vi"])
    browser = FakeBrowser()
    await queue.put((TTS_TEXT, "dòng một", "vi", 11))
    await queue.put((TTS_TEXT, "dòng hai", "vi", 12))
    await queue.put((TTS_END, "vi"))
    await queue.put(TTS_NONE)

    await external_tts_sender(
        tts_queue=queue,
        tts_state=state,
        browser_ws=browser,
        provider_id="openai",
        provider=provider,
        direction_voices={"vi": "nova"},
    )

    metas = [message for message in browser.json_messages if message.get("type") == "audio_chunk_meta"]
    assert [meta["line_id"] for meta in metas] == [11, 12]
    assert all(meta["line_audio_end"] is True for meta in metas)
    assert len(browser.audio) == 2
