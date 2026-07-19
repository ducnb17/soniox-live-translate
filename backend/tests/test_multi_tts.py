import asyncio

from app.external_tts import external_tts_sender
from app.stt import TTS_END, TTS_NONE, TTS_TEXT
from app.tts import new_tts_state
from app.tts_provider import TTSCache, get_available_providers, get_provider, tts_cache


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
    await queue.put((TTS_TEXT, "xin chào", "vi"))
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
    assert {info.id for info in get_available_providers()} == expected
    for provider_id in expected:
        provider = get_provider(provider_id)
        assert provider is not None
        assert await provider.list_voices(lang="en")


def test_tts_cache_is_true_lru_and_bounded_by_entries_and_bytes():
    cache = TTSCache(max_size=2, max_bytes=8)
    cache.set("one", "voice", "provider", b"1111")
    cache.set("two", "voice", "provider", b"2222")
    assert cache.get("one", "voice", "provider") == b"1111"  # promote one
    cache.set("three", "voice", "provider", b"3333")
    assert cache.get("two", "voice", "provider") is None
    assert cache.entry_count == 2
    assert cache.total_bytes == 8
    cache.set("oversized", "voice", "provider", b"x" * 9)
    assert cache.get("oversized", "voice", "provider") is None


async def test_external_provider_synthesizes_then_reuses_cache_with_zero_second_cost():
    tts_cache.clear()
    provider = SuccessfulProvider()

    first = await run_sender(provider)
    second = await run_sender(provider)

    assert provider.calls == 1
    assert first.audio == [b"pcm:nova:vi:xin ch\xc3\xa0o"]
    first_usage = next(message["tts_usage"] for message in first.json_messages if "tts_usage" in message)
    second_usage = next(message["tts_usage"] for message in second.json_messages if "tts_usage" in message)
    assert first_usage["characters"] == len("xin chào")
    assert first_usage["estimated_cost_usd"] == len("xin chào") * 0.000015
    assert first_usage["cache_hit"] is False
    assert second_usage["estimated_cost_usd"] == 0.0
    assert second_usage["cache_hit"] is True


async def test_provider_quota_error_notifies_user_and_falls_back_to_soniox():
    tts_cache.clear()

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
