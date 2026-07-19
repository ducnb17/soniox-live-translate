"""ElevenLabs TTS provider.

Highest quality voices, supports voice cloning and streaming.
Pricing: verify at https://elevenlabs.io/pricing
"""

import io
import wave
from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger
from ..provider_connection import test_get

log = get_logger("elevenlabs_tts")

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"


@register_provider
class ElevenLabsProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._voices_cache: list[Voice] | None = None

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "ElevenLabs API key is required"
        return await test_get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": self._api_key},
        )

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        if not self._api_key:
            return self._default_voices()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(
                    ELEVENLABS_VOICES_URL,
                    headers={"xi-api-key": self._api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                voices = []
                for v in data.get("voices", []):
                    name = v.get("name", "Unknown")
                    vid = v.get("voice_id", "")
                    voices.append(Voice(
                        id=vid, name=name,
                        language=lang or "en", gender="neutral",
                        provider_id="elevenlabs",
                    ))
                return voices
        except Exception as e:
            log.warning("elevenlabs_voice_list_failed", error=str(e))
            return self._default_voices()

    def _default_voices(self) -> list[Voice]:
        return [
            Voice(id="21m00Tcm4TlvDq8ikWAM", name="Rachel (default)", language="en"),
            Voice(id="AZnzlk1XvdvUeBnXmlld", name="Domi", language="en"),
            Voice(id="EXAVITQu4vr4xnSDxMaL", name="Bella", language="en"),
            Voice(id="ErXwobaYiN019PkySvjV", name="Antoni", language="en"),
            Voice(id="MF3mGyEYCl7XYWbV9V6O", name="Elli", language="en"),
            Voice(id="TxGEqnHWrfWFTfGW9XjX", name="Josh", language="en"),
            Voice(id="VR6AewLTigWG4xSOukaG", name="Arnold", language="en"),
            Voice(id="pNInz6obpgDQGcFmaJgB", name="Adam", language="en"),
            Voice(id="yoZ06aMxZJJ28mfd3POQ", name="Sam", language="en"),
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise ValueError("ElevenLabs API key not configured")

        url = f"{ELEVENLABS_URL}/{voice_id}/stream"
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "output_format": "pcm_24000",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            async with client.stream(
                "POST", url, json=payload,
                headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk

    def estimate_cost(self, char_count: int) -> float:
        # ~$0.18/1000 chars for Turbo v2.5 = $180/million
        # Verify at https://elevenlabs.io/pricing
        return char_count * 0.00018

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="elevenlabs",
            name="ElevenLabs",
            description="Highest quality AI voices, voice cloning, real-time streaming. Expensive but natural.",
            requires_api_key=True,
            supports_streaming=True,
            tier="premium",
            pricing_url="https://elevenlabs.io/pricing",
            approximate_cost_per_1m_chars=180.0,
        )
