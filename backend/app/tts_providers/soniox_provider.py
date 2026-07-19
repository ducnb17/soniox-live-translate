"""Soniox built-in TTS provider (default, no API key needed beyond Soniox key)."""

import json
from typing import AsyncIterator

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..config import VOICES as SONIOX_VOICES
from .. import config
from ..provider_connection import test_get


@register_provider
class SonioxProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        api_key = self._api_key or config.SONIOX_API_KEY
        if not api_key:
            return False, "Soniox API key is required"
        return await test_get(
            "https://api.soniox.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        voices = []
        for name in SONIOX_VOICES:
            voices.append(Voice(
                id=name,
                name=name,
                language=lang or "en",
                gender="neutral",
                provider_id="soniox",
            ))
        return voices

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        raise NotImplementedError("Soniox TTS is handled via the existing WebSocket pipeline")

    def estimate_cost(self, char_count: int) -> float:
        return 0.0

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="soniox",
            name="Soniox Built-in",
            description="12 built-in voices, included in Soniox API. No extra key needed.",
            requires_api_key=False,
            supports_streaming=True,
            tier="cheap",
            pricing_url="https://soniox.com/pricing",
            approximate_cost_per_1m_chars=0.0,
        )
