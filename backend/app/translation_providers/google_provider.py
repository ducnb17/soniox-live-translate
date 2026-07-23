import httpx

from ..provider_connection import test_get
from ..translation_provider import TranslationProviderBase, TranslationProviderInfo, register_provider


@register_provider
class GoogleTranslationProvider(TranslationProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def translate(
        self,
        text: str,
        source_lang: str | None,
        target_lang: str,
        style: str = "natural",
    ) -> str:
        if not self._api_key:
            raise ValueError("Google Cloud API key is required")
        payload: dict[str, object] = {"q": text, "target": target_lang, "format": "text"}
        if source_lang:
            payload["source"] = source_lang
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": self._api_key},
                json=payload,
            )
            response.raise_for_status()
        translations = response.json()["data"]["translations"]
        return str(translations[0]["translatedText"])

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "Google Cloud API key is required"
        return await test_get(
            "https://translation.googleapis.com/language/translate/v2/languages",
            params={"key": self._api_key, "target": "en"},
        )

    @property
    def info(self) -> TranslationProviderInfo:
        return TranslationProviderInfo(
            id="google",
            name="Google Cloud Translation",
            description="Broad language coverage with a separate request after each utterance.",
            requires_api_key=True,
            supports_realtime_translation=False,
            tier="cheap",
            pricing_url="https://cloud.google.com/translate/pricing",
            signup_url="https://console.cloud.google.com/apis/credentials",
        )
