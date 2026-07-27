"""AssemblyAI Universal STT descriptor and credential check."""

from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider
from ..provider_connection import test_get


@register_provider
class AssemblyAISTTProvider(STTProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "AssemblyAI API key is required"
        return await test_get(
            "https://api.assemblyai.com/v2/transcript",
            headers={"Authorization": self._api_key},
            params={"limit": 1},
        )

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="assemblyai",
            name="AssemblyAI Universal Streaming",
            description="Low-latency streaming STT with a substantial free allowance for new accounts.",
            requires_api_key=True,
            supports_streaming=True,
            supports_realtime_translation=False,
            tier="free",
            pricing_url="https://www.assemblyai.com/pricing",
            approximate_cost_per_hour=0.15,
        )
