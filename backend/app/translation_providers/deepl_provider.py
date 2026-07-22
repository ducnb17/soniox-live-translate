import httpx

from ..provider_connection import test_get
from ..translation_provider import TranslationProviderBase, TranslationProviderInfo, register_provider


@register_provider
class DeepLTranslationProvider(TranslationProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _base_url(self) -> str:
        return "https://api-free.deepl.com" if (self._api_key or "").endswith(":fx") else "https://api.deepl.com"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"DeepL-Auth-Key {self._api_key}"}

    async def translate(
        self,
        text: str,
        source_lang: str | None,
        target_lang: str,
        style: str = "natural",
    ) -> str:
        if not self._api_key:
            raise ValueError("DeepL API key is required")
        data: dict[str, object] = {"text": [text], "target_lang": target_lang.upper()}
        if source_lang:
            data["source_lang"] = source_lang.upper()
        if style == "professional":
            data["formality"] = "prefer_more"
        elif style == "casual":
            data["formality"] = "prefer_less"
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.post(
                f"{self._base_url()}/v2/translate",
                headers=self._headers(),
                json=data,
            )
            response.raise_for_status()
        return str(response.json()["translations"][0]["text"])

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "DeepL API key is required"
        return await test_get(f"{self._base_url()}/v2/usage", headers=self._headers())

    @property
    def info(self) -> TranslationProviderInfo:
        return TranslationProviderInfo(
            id="deepl",
            name="DeepL API",
            description="High-quality neural translation, especially for European languages.",
            requires_api_key=True,
            supports_realtime_translation=False,
            tier="premium",
            pricing_url="https://www.deepl.com/pro-api",
            signup_url="https://www.deepl.com/pro-api",
            supported_styles=("natural", "professional", "casual"),
        )
