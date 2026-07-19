import httpx

from ..provider_connection import test_get
from ..translation_provider import TranslationProviderBase, TranslationProviderInfo, register_provider


@register_provider
class OpenAITranslationProvider(TranslationProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def translate(self, text: str, source_lang: str | None, target_lang: str) -> str:
        if not self._api_key:
            raise ValueError("OpenAI API key is required")
        source = source_lang or "auto-detected language"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={
                    "model": "gpt-5.4-nano",
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": "Translate faithfully. Return only the translation."},
                        {"role": "user", "content": f"Translate from {source} to {target_lang}:\n{text}"},
                    ],
                },
            )
            response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]).strip()

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "OpenAI API key is required"
        return await test_get("https://api.openai.com/v1/models", headers=self._headers())

    @property
    def info(self) -> TranslationProviderInfo:
        return TranslationProviderInfo(
            id="openai",
            name="OpenAI Translation",
            description="Context-aware translation using a language model after each utterance.",
            requires_api_key=True,
            supports_realtime_translation=False,
            tier="premium",
            pricing_url="https://openai.com/api/pricing",
            signup_url="https://platform.openai.com/api-keys",
        )
