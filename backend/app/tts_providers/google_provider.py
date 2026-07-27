"""Google Cloud Text-to-Speech provider (Chirp 3 HD voices).

Uses Google Cloud TTS REST API. Supports streaming synthesis.
Pricing should be verified at: https://cloud.google.com/text-to-speech/pricing
"""

import base64
import json
from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger
from ..provider_connection import test_get

log = get_logger("google_tts")

GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
GOOGLE_VI_VOICES = [
    "vi-VN-Chirp3-HD-Aoede", "vi-VN-Chirp3-HD-Charon", "vi-VN-Chirp3-HD-Fenrir",
    "vi-VN-Chirp3-HD-Kore", "vi-VN-Chirp3-HD-Leda", "vi-VN-Chirp3-HD-Orus",
    "vi-VN-Chirp3-HD-Puck", "vi-VN-Chirp3-HD-Standard-A",
    "vi-VN-Standard-A", "vi-VN-Standard-B", "vi-VN-Standard-C", "vi-VN-Standard-D",
    "vi-VN-Wavenet-A", "vi-VN-Wavenet-B", "vi-VN-Wavenet-C", "vi-VN-Wavenet-D",
]
GOOGLE_EN_VOICES = [
    "en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Charon", "en-US-Chirp3-HD-Fenrir",
    "en-US-Chirp3-HD-Kore", "en-US-Chirp3-HD-Leda", "en-US-Chirp3-HD-Orus",
    "en-US-Chirp3-HD-Puck",
    "en-US-Standard-A", "en-US-Standard-B", "en-US-Standard-C", "en-US-Standard-D",
    "en-US-Wavenet-A", "en-US-Wavenet-B", "en-US-Wavenet-C", "en-US-Wavenet-D",
]
OTHER_VOICES = [  # Common languages
    "ja-JP-Chirp3-HD-Aoede", "ja-JP-Chirp3-HD-Charon",
    "ko-KR-Chirp3-HD-Aoede", "ko-KR-Chirp3-HD-Charon",
    "zh-CN-Chirp3-HD-Aoede", "zh-CN-Chirp3-HD-Charon",
    "es-ES-Chirp3-HD-Aoede", "es-ES-Chirp3-HD-Charon",
    "fr-FR-Chirp3-HD-Aoede", "fr-FR-Chirp3-HD-Charon",
    "de-DE-Chirp3-HD-Aoede", "de-DE-Chirp3-HD-Charon",
]


@register_provider
class GoogleTTSProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "Google Cloud API key is required"
        return await test_get(
            "https://texttospeech.googleapis.com/v1/voices",
            params={"key": self._api_key},
        )

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        voices = []
        name_map: dict[str, list[str]] = {
            "vi": GOOGLE_VI_VOICES,
            "en": GOOGLE_EN_VOICES,
        }
        name_map.update({v.split("-")[0]: [v] for v in OTHER_VOICES})

        candidates = []
        if lang and lang in name_map:
            candidates = name_map[lang]
        elif lang:
            prefix = lang.replace("_", "-")
            candidates = [v for v in OTHER_VOICES if v.startswith(prefix)]
        if not candidates:
            candidates = [*GOOGLE_EN_VOICES, *GOOGLE_VI_VOICES, *OTHER_VOICES]

        for voice_name in candidates:
            parts = voice_name.split("-", 2)
            voice_lang = f"{parts[0]}-{parts[1]}" if len(parts) >= 3 else voice_name.split("-", 1)[0]
            is_chirp = "Chirp3" in voice_name
            voices.append(Voice(
                id=voice_name,
                name=f"{voice_name} {'(HD)' if is_chirp else ''}",
                language=voice_lang,
                gender="neutral",
                provider_id="google",
            ))
        return voices

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise ValueError("Google Cloud API key not configured")

        lang_code = voice_id.split("-", 2)[0] + "-" + voice_id.split("-", 2)[1] if "-" in voice_id else lang
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": lang_code, "name": voice_id},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": 24000,
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(
                f"{GOOGLE_TTS_URL}?key={self._api_key}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            audio_b64 = data.get("audioContent", "")
            if audio_b64:
                yield base64.b64decode(audio_b64)

    def estimate_cost(self, char_count: int) -> float:
        # Standard voices ~$4/million, WaveNet ~$16/million, Chirp3 HD ~$32/million
        # Prices change; verify at https://cloud.google.com/text-to-speech/pricing
        return char_count * 0.000016

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="google",
            name="Google Cloud TTS (Chirp3 HD)",
            description="High-quality HD voices for Vietnamese & 40+ languages. Supports streaming.",
            requires_api_key=True,
            supports_streaming=False,
            tier="cheap",
            pricing_url="https://cloud.google.com/text-to-speech/pricing",
            approximate_cost_per_1m_chars=16.0,
        )
