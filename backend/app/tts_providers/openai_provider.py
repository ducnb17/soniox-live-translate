"""OpenAI TTS provider.

Uses OpenAI TTS API (tts-1 / tts-1-hd). OpenAI doesn't support true streaming
TTS, but tts-1 has low latency.
Pricing: verify at https://openai.com/pricing
"""

import base64
import struct
from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger

log = get_logger("openai_tts")

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


@register_provider
class OpenAITTSProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        return [
            Voice(id=v, name=v.capitalize(), language=lang or "en", gender="neutral", provider_id="openai")
            for v in OPENAI_VOICES
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise ValueError("OpenAI API key not configured")

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice_id,
            "response_format": "pcm",
            "speed": 1.0,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(
                OPENAI_TTS_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            # OpenAI returns 24kHz pcm_s16le raw bytes directly
            yield resp.content

    def estimate_cost(self, char_count: int) -> float:
        # tts-1: $15/million chars, tts-1-hd: $30/million chars
        # Verify at https://openai.com/pricing
        return char_count * 0.000015

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="openai",
            name="OpenAI TTS",
            description="6 voices (tts-1/tts-1-hd). Low latency but no true streaming.",
            requires_api_key=True,
            supports_streaming=False,
            pricing_url="https://openai.com/pricing",
            approximate_cost_per_1m_chars=15.0,
        )
