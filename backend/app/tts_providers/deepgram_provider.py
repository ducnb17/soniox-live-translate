"""Deepgram Aura TTS provider.

Optimized for real-time / low latency, good for live call translation.
Pricing: verify at https://deepgram.com/pricing
"""

from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger
from ..provider_connection import test_get

log = get_logger("deepgram_tts")

DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_VOICES = [
    "aura-asteria-en", "aura-luna-en", "aura-stella-en",
    "aura-athena-en", "aura-hera-en", "aura-orion-en",
    "aura-arcas-en", "aura-perseus-en", "aura-angus-en",
    "aura-orpheus-en", "aura-helios-en", "aura-zeus-en",
]


@register_provider
class DeepgramProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "Deepgram API key is required"
        return await test_get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {self._api_key}"},
        )

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        return [
            Voice(id=v, name=v.replace("aura-", "").replace("-en", "").capitalize(),
                  language="en", gender="neutral", provider_id="deepgram")
            for v in DEEPGRAM_VOICES
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise ValueError("Deepgram API key not configured")

        url = f"{DEEPGRAM_TTS_URL}?model={voice_id}&encoding=linear16&sample_rate=24000"
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(url, json={"text": text}, headers=headers)
            resp.raise_for_status()
            yield resp.content

    def estimate_cost(self, char_count: int) -> float:
        # ~$15/million chars for Aura
        # Verify at https://deepgram.com/pricing
        return char_count * 0.000015

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="deepgram",
            name="Deepgram Aura",
            description="Real-time low-latency TTS, optimized for live applications.",
            requires_api_key=True,
            supports_streaming=False,
            tier="cheap",
            pricing_url="https://deepgram.com/pricing",
            approximate_cost_per_1m_chars=15.0,
        )
