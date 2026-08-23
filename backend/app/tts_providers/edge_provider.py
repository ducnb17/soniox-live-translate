"""Edge TTS provider (Microsoft Edge online neural voices).

Completely free, no API key required. Uses the same websocket endpoint as
Microsoft Edge's "Read Aloud" feature via the `edge-tts` package. Supports
Vietnamese and 100+ languages with natural neural voices.

Note: requires network access to speech.platform.bing.com. Voices list is
fetched live; a curated fallback list is used when the endpoint is unreachable.
"""

from __future__ import annotations

import asyncio
import io
import wave
from typing import AsyncIterator

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger

log = get_logger("edge_tts")

# Curated fallback voices per language (id -> display name). Used when the
# live voice list cannot be fetched (offline / endpoint blocked).
FALLBACK_VOICES: dict[str, list[tuple[str, str]]] = {
    "vi": [
        ("vi-VN-HoaiMyNeural", "Hoai My (Female)"),
        ("vi-VN-NamMinhNeural", "Nam Minh (Male)"),
    ],
    "en": [
        ("en-US-JennyNeural", "Jenny (Female, US)"),
        ("en-US-ChristopherNeural", "Christopher (Male, US)"),
        ("en-GB-SoniaNeural", "Sonia (Female, UK)"),
        ("en-GB-RyanNeural", "Ryan (Male, UK)"),
    ],
}

# Language code used by the app -> BCP-47 code used by Edge (mostly identical).
_LANG_ALIASES = {
    "vi": "vi-VN",
    "en": "en-US",
    "en_us": "en-US",
    "en_gb": "en-GB",
}


def _edge_lang(lang: str) -> str:
    return _LANG_ALIASES.get(lang, lang)


@register_provider
class EdgeTTSProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            return False, f"edge-tts is not installed: {exc}"
        try:
            voices = await edge_tts.list_voices()
            return True, f"OK ({len(voices)} voices available)"
        except Exception as exc:  # network unreachable etc.
            return False, f"edge-tts endpoint unreachable: {exc}"

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        try:
            import edge_tts

            all_voices = await edge_tts.list_voices()
            if lang:
                # Match by locale prefix (e.g. "vi" matches "vi-VN-*")
                all_voices = [
                    v for v in all_voices
                    if (v.get("Locale") or "").lower().startswith(lang.lower())
                ]
            voices = [
                Voice(
                    id=v["ShortName"],
                    name=f"{v.get('FriendlyName', v['ShortName'])} ({v.get('Gender', '')})",
                    language=(v.get("Locale") or lang or "en"),
                    gender=str(v.get("Gender", "neutral")).lower(),
                    provider_id="edge_tts",
                )
                for v in all_voices
            ]
            if voices:
                return voices
        except Exception as exc:
            log.warning("edge-tts list_voices failed (%s); using fallback", exc)

        # Fallback list (offline or endpoint blocked)
        pairs = FALLBACK_VOICES.get(lang or "", FALLBACK_VOICES.get("en", []))
        return [
            Voice(id=vid, name=name, language=lang or "en", gender="neutral", provider_id="edge_tts")
            for vid, name in pairs
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        import edge_tts

        voice = voice_id or FALLBACK_VOICES.get(_edge_lang(lang), FALLBACK_VOICES["en"])[0][0]
        communicate = edge_tts.Communicate(text=text, voice=voice)

        # Run the edge-tts producer and the ffmpeg consumer concurrently on a
        # streaming pipe. We write mp3 chunks to ffmpeg as they arrive, and yield
        # decoded PCM chunks as they come out -- no wait for the full utterance.
        mp3_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def producer() -> None:
            try:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        await mp3_queue.put(chunk["data"])
            finally:
                await mp3_queue.put(None)

        producer_task = asyncio.create_task(producer())
        try:
            ffmpeg_proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-f", "s16le", "-ac", "1", "-ar", "24000",
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert ffmpeg_proc.stdin is not None
            assert ffmpeg_proc.stdout is not None
            assert ffmpeg_proc.stderr is not None

            async def feed_ffmpeg() -> None:
                try:
                    while True:
                        mp3 = await mp3_queue.get()
                        if mp3 is None:
                            break
                        ffmpeg_proc.stdin.write(mp3)
                        await ffmpeg_proc.stdin.drain()
                finally:
                    try:
                        ffmpeg_proc.stdin.close()
                        await ffmpeg_proc.stdin.wait_closed()
                    except Exception:
                        pass

            feed_task = asyncio.create_task(feed_ffmpeg())

            try:
                while True:
                    pcm = await ffmpeg_proc.stdout.read(4096)
                    if not pcm:
                        break
                    yield pcm
            finally:
                feed_task.cancel()
                try:
                    await feed_task
                except asyncio.CancelledError:
                    pass
                returncode = await ffmpeg_proc.wait()
                if returncode not in (0, -13, -15, 255):
                    err = (await ffmpeg_proc.stderr.read()).decode(errors="replace")[:200]
                    raise RuntimeError(f"ffmpeg streaming failed ({returncode}): {err}")
        finally:
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    def estimate_cost(self, char_count: int) -> float:
        return 0.0

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="edge_tts",
            name="Edge TTS (free, online)",
            description=(
                "Microsoft Edge neural voices — completely free, no API key. "
                "Supports Vietnamese and 100+ languages. Requires internet."
            ),
            requires_api_key=False,
            supports_streaming=False,
            tier="free",
            pricing_url="",
            approximate_cost_per_1m_chars=0.0,
        )
