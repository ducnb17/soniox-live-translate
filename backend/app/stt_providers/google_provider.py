"""Google Cloud Speech-to-Text descriptor and credential check."""

from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider
from ..provider_connection import test_get


@register_provider
class GoogleSTTProvider(STTProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "Google Cloud API key is required"
        return await test_get(
            "https://speech.googleapis.com/v1/operations",
            params={"key": self._api_key, "pageSize": 1},
        )

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="google",
            name="Google Cloud Speech-to-Text",
            description="Cloud STT V2 with broad language coverage and genuine streaming recognition.",
            requires_api_key=True,
            supports_streaming=True,
            supports_realtime_translation=False,
            tier="premium",
            pricing_url="https://cloud.google.com/speech-to-text/pricing",
            approximate_cost_per_hour=0.96,
        )
