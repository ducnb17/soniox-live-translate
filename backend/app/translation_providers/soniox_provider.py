from .. import config
from ..provider_connection import test_get
from ..translation_provider import (
    TranslationProviderBase,
    TranslationProviderInfo,
    register_provider,
)


@register_provider
class SonioxTranslationProvider(TranslationProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def translate(
        self,
        text: str,
        source_lang: str | None,
        target_lang: str,
        style: str = "natural",
    ) -> str:
        raise NotImplementedError("Soniox translation runs inside the existing STT stream")

    async def test_connection(self) -> tuple[bool, str]:
        api_key = self._api_key or config.SONIOX_API_KEY
        if not api_key:
            return False, "Soniox API key is required"
        return await test_get(
            "https://api.soniox.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @property
    def info(self) -> TranslationProviderInfo:
        return TranslationProviderInfo(
            id="soniox",
            name="Soniox realtime translation (Default)",
            description="Lowest-latency translation produced in the same stream as transcription.",
            requires_api_key=False,
            supports_realtime_translation=True,
            tier="cheap",
            pricing_url="https://soniox.com/pricing",
            signup_url="https://console.soniox.com",
        )
