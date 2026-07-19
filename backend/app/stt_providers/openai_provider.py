"""OpenAI Whisper API STT descriptor and credential check."""

from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider
from ..provider_connection import test_get


@register_provider
class OpenAIWhisperProvider(STTProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "OpenAI API key is required"
        return await test_get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="openai",
            name="OpenAI Whisper API",
            description="Accurate multilingual file transcription; Whisper API has no true live streaming.",
            requires_api_key=True,
            supports_streaming=False,
            supports_realtime_translation=False,
            tier="cheap",
            pricing_url="https://developers.openai.com/api/docs/models/whisper-1",
            approximate_cost_per_hour=0.36,
        )
