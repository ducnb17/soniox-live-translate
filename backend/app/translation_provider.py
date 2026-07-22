"""Translation provider abstraction and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


VALID_PROVIDER_TIERS = frozenset({"free", "cheap", "premium"})


@dataclass
class TranslationProviderInfo:
    id: str
    name: str
    description: str
    requires_api_key: bool
    supports_realtime_translation: bool
    tier: str
    pricing_url: str
    signup_url: str
    provider_class: str = ""
    supported_styles: tuple[str, ...] = ("natural",)

    def __post_init__(self) -> None:
        if self.tier not in VALID_PROVIDER_TIERS:
            allowed = ", ".join(sorted(VALID_PROVIDER_TIERS))
            raise ValueError(f"tier must be one of: {allowed}")


class TranslationProviderBase(ABC):
    @abstractmethod
    async def translate(
        self,
        text: str,
        source_lang: str | None,
        target_lang: str,
        style: str = "natural",
    ) -> str:
        ...

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        ...

    @property
    @abstractmethod
    def info(self) -> TranslationProviderInfo:
        ...


_provider_registry: dict[str, type[TranslationProviderBase]] = {}


def register_provider(
    provider_cls: type[TranslationProviderBase],
) -> type[TranslationProviderBase]:
    instance = provider_cls(api_key=None)
    _provider_registry[instance.info.id] = provider_cls
    return provider_cls


def get_provider(
    provider_id: str,
    api_key: str | None = None,
) -> TranslationProviderBase | None:
    provider_cls = _provider_registry.get(provider_id)
    return provider_cls(api_key=api_key) if provider_cls else None


def get_available_providers() -> list[TranslationProviderInfo]:
    return [provider_cls(api_key=None).info for provider_cls in _provider_registry.values()]


from .translation_providers.soniox_provider import SonioxTranslationProvider  # noqa: E402
from .translation_providers.google_provider import GoogleTranslationProvider  # noqa: E402
from .translation_providers.deepl_provider import DeepLTranslationProvider  # noqa: E402
from .translation_providers.openai_provider import OpenAITranslationProvider  # noqa: E402
