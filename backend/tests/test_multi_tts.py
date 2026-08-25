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
        # Fake PCM s16le: 50ms of 1000Hz tone @24kHz (2400 samples) — real
        # audio-like data so silence-trim logic doesn't discard it.
        import math
        import struct
        samples = [
            struct.pack("<h", int(20000 * math.sin(2 * math.pi * 1000 * i / 24000)))
            for i in range(2400)
        ]
        yield b"".join(samples)

    def estimate_cost(self, char_count):
        return char_count * 0.000015


class FailingProvider:
    async def synthesize_stream(self, text, voice_id, lang):
        # Async generator that immediately raises without yielding anything.
        if False:
            yield b""  # make this an async generator
        raise RuntimeError("quota exhausted")

    def estimate_cost(self, char_count):
        return 0.0


async def run_sender(provider, provider_id="openai", fallback=None, wait_for_playback=True):
    queue = asyncio.Queue()
    state = new_tts_state(["vi"])
    browser = FakeBrowser()
    await queue.put((TTS_TEXT, "xin chào", "vi", 1))
    await queue.put((TTS_END, "vi"))
    await queue.put(TTS_NONE)
    kwargs = {}
    if fallback is not None:
        kwargs["fallback_synthesize"] = fallback
    await asyncio.wait_for(
        external_tts_sender(
            tts_queue=queue,
            tts_state=state,
            browser_ws=browser,
            provider_id=provider_id,
            provider=provider,
            direction_voices={"vi": "nova"},
            **kwargs,
        ),
        timeout=10.0,
    )
    return browser


async def test_all_providers_are_registered_and_have_voices():
    expected = {"soniox", "google", "openai", "azure", "elevenlabs", "deepgram", "polly", "pocket_tts", "edge_tts", "piper_gpu"}
    infos = get_available_providers()
    assert {info.id for info in infos} == expected
    assert {info.tier for info in infos} <= {"free", "cheap", "premium"}
    assert next(info for info in infos if info.id == "elevenlabs").tier == "premium"
    for provider_id in expected:
        provider = get_provider(provider_id)
        assert provider is not None
        assert await provider.list_voices(lang="vi" if provider_id == "piper_gpu" else "en")


async def test_piper_gpu_offers_three_labeled_vietnamese_voices():
    provider = get_provider("piper_gpu")
    assert provider is not None
    voices = await provider.list_voices(lang="vi")
    assert [(voice.id, voice.gender) for voice in voices] == [
        ("vi_VN-vais1000-medium", "female"),
        ("vi_VN-25hours_single-low", "female"),
        ("vi_VN-vivos-x_low", "male"),
    ]
    assert all("GPU" in voice.name for voice in voices)


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
    # Audio is a 1000Hz tone (4800 bytes PCM s16le); silence-trim may cut a
    # few edge samples, so just assert it's non-empty and PCM-like.
    assert first.audio and len(first.audio[0]) >= 4000
    meta = next(m for m in first.json_messages if m.get("type") == "audio_chunk_meta")
    assert meta == {
        "type": "audio_chunk_meta",
        "line_id": 1,
        "byte_length": len(first.audio[0]),
        "line_audio_end": True,
    }
    first_usage = next((message["tts_usage"] for message in first.json_messages if "tts_usage" in message), None)
    second_usage = next((message["tts_usage"] for message in second.json_messages if "tts_usage" in message), None)
    assert first_usage is not None
    assert second_usage is not None
    assert first_usage["characters"] == len("xin chào")
    assert first_usage["estimated_cost_usd"] == len("xin chào") * 0.000015
    assert first_usage["cache_hit"] is False
    assert second_usage["estimated_cost_usd"] == 0.0
    assert second_usage["cache_hit"] is True


async def test_provider_quota_error_notifies_user_and_falls_back_to_soniox():
    tts_cache.clear()

    async def fallback(text, voice, lang):
        assert (text, voice, lang) == ("xin chào", "Maya", "vi")
        # Fake PCM tone so silence-trim keeps it (same shape as provider data).
        import math
        import struct
        return b"".join(
            struct.pack("<h", int(20000 * math.sin(2 * math.pi * 1000 * i / 24000)))
            for i in range(2400)
        )

    browser = await run_sender(FailingProvider(), provider_id="openai", fallback=fallback)

    fallback_event = next((message["tts_fallback"] for message in browser.json_messages if "tts_fallback" in message), None)
    assert fallback_event is not None, f"No tts_fallback in {browser.json_messages}"
    assert fallback_event == {
        "from_provider": "openai",
        "to_provider": "soniox",
        "reason": "quota exhausted",
    }
    assert browser.audio and len(browser.audio[0]) >= 4000
    usage = next((message["tts_usage"] for message in browser.json_messages if "tts_usage" in message), None)
    assert usage is not None, f"No tts_usage in {browser.json_messages}"
    assert usage["provider_id"] == "soniox"
    assert usage["characters"] == len("xin chào")


async def test_external_provider_keeps_each_line_as_separate_labeled_audio():
    tts_cache.clear()
    provider = SuccessfulProvider()
    queue = asyncio.Queue()
    state = new_tts_state(["vi"])
    browser = FakeBrowser()
    # Each rendered translation line must be synthesized with its own line ID.
    # Otherwise the frontend's strict ordered queue waits forever on the first
    # registered line, while audio is incorrectly labelled as a later line.
    await queue.put((TTS_TEXT, "dòng một.", "vi", 11))
    await queue.put((TTS_TEXT, "dòng hai.", "vi", 12))
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
