"""Deepgram Nova STT descriptor and credential check."""

from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider
from ..provider_connection import test_get


@register_provider
class DeepgramSTTProvider(STTProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "Deepgram API key is required"
        return await test_get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {self._api_key}"},
        )

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="deepgram",
            name="Deepgram Nova-3",
            description="Low-latency real-time and pre-recorded transcription with multilingual models.",
            requires_api_key=True,
            supports_streaming=True,
            supports_realtime_translation=False,
            tier="cheap",
            pricing_url="https://deepgram.com/pricing",
            approximate_cost_per_hour=0.29,
        )
