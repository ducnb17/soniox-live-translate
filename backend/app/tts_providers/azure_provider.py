"""Microsoft Azure Neural TTS provider.

Uses Azure Cognitive Services Speech REST API with SSML.
Supports streaming via chunked transfer encoding.
Pricing: verify at https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/
"""

from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger

log = get_logger("azure_tts")

AZURE_VOICES_VI = [
    "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural",
]
AZURE_VOICES_EN = [
    "en-US-JennyNeural", "en-US-GuyNeural", "en-US-AriaNeural",
    "en-US-DavisNeural", "en-US-JasonNeural", "en-US-SaraNeural",
    "en-US-TonyNeural", "en-US-NancyNeural",
]
AZURE_VOICES_COMMON = [
    "ja-JP-NanamiNeural", "ja-JP-KeitaNeural",
    "ko-KR-SunHiNeural", "ko-KR-InJoonNeural",
    "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural",
    "fr-FR-DeniseNeural", "fr-FR-HenriNeural",
    "de-DE-KatjaNeural", "de-DE-ConradNeural",
    "es-ES-ElviraNeural", "es-ES-AlvaroNeural",
]


@register_provider
class AzureTTSProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._region = "eastus"  # default region

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        voices = []
        candidates = [*AZURE_VOICES_EN, *AZURE_VOICES_VI, *AZURE_VOICES_COMMON]
        if lang:
            prefix = lang.replace("_", "-")
            candidates = [v for v in candidates if v.startswith(prefix)]
        for vn in candidates:
            parts = vn.split("-", 2)
            voice_lang = f"{parts[0]}-{parts[1]}" if len(parts) >= 3 else parts[0]
            voices.append(Voice(
                id=vn, name=vn.replace("Neural", "").replace("-", " "),
                language=voice_lang, gender="neutral", provider_id="azure",
            ))
        return voices

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise ValueError("Azure Speech API key not configured")

        lang_code = voice_id.split("-", 2)[0] + "-" + voice_id.split("-", 2)[1] if "-" in voice_id else lang
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{lang_code}">'
            f'<voice name="{voice_id}">{text}</voice></speak>'
        )

        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm",
            "User-Agent": "SonioxLiveTranslate",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            async with client.stream("POST", url, content=ssml, headers=headers) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk

    def estimate_cost(self, char_count: int) -> float:
        # Neural: ~$15/million chars
        # Verify at Azure pricing page
        return char_count * 0.000015

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="azure",
            name="Azure Neural TTS",
            description="Microsoft neural voices for 100+ languages. Streaming support.",
            requires_api_key=True,
            supports_streaming=True,
            pricing_url="https://azure.microsoft.com/pricing/details/cognitive-services/speech-services/",
            approximate_cost_per_1m_chars=15.0,
        )
