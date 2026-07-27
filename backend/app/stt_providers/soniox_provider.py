"""Soniox STT descriptor.

Audio remains handled by the established implementation in ``stt.py`` and
``context_builder.py``; this class only exposes registry metadata and a
lightweight credential check.
"""

from .. import config
from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider
from ..provider_connection import test_get


@register_provider
class SonioxSTTProvider(STTProviderBase):
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

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="soniox",
            name="Soniox",
            description="Low-latency multilingual STT with translation in the same real-time stream.",
            requires_api_key=False,
            supports_streaming=True,
            supports_realtime_translation=True,
            tier="cheap",
            pricing_url="https://soniox.com/pricing",
            approximate_cost_per_hour=0.12,
        )
