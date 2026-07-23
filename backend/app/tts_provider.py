"""TTS provider abstraction layer.

Each provider implements the TTSProviderBase interface. The factory
`get_provider()` returns an instance by provider ID. New providers
can be added by implementing the interface without touching the UI.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

from .logging_config import get_logger

log = get_logger("tts_provider")

VALID_PROVIDER_TIERS = frozenset({"free", "cheap", "premium"})


@dataclass
class Voice:
    id: str
    name: str
    language: str
    gender: str = "neutral"
    provider_id: str = ""


@dataclass
class TTSProviderInfo:
    id: str
    name: str
    description: str
    requires_api_key: bool
    supports_streaming: bool
    tier: str
    pricing_url: str
    approximate_cost_per_1m_chars: float = 0.0
    provider_class: str = ""

    def __post_init__(self) -> None:
        if self.tier not in VALID_PROVIDER_TIERS:
            allowed = ", ".join(sorted(VALID_PROVIDER_TIERS))
            raise ValueError(f"tier must be one of: {allowed}")


class TTSProviderBase(ABC):
    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        ...

    @abstractmethod
    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        ...

    @abstractmethod
    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        """Yield PCM s16le audio chunks at 24000 Hz sample rate."""
        ...

    @abstractmethod
    def estimate_cost(self, char_count: int) -> float:
        ...

    @property
    @abstractmethod
    def info(self) -> TTSProviderInfo:
        ...


# ── Provider factory ──

_provider_registry: dict[str, type[TTSProviderBase]] = {}


def register_provider(provider_cls: type[TTSProviderBase]) -> type[TTSProviderBase]:
    inst = provider_cls(api_key=None)
    _provider_registry[inst.info.id] = provider_cls
    return provider_cls


def get_provider(provider_id: str, api_key: str | None = None) -> TTSProviderBase | None:
    cls = _provider_registry.get(provider_id)
    if cls is None:
        return None
    return cls(api_key=api_key)


def get_available_providers() -> list[TTSProviderInfo]:
    result = []
    for pid, cls in _provider_registry.items():
        inst = cls(api_key=None)
        result.append(inst.info)
    return result


# Import and register all providers
from .tts_providers.soniox_provider import SonioxProvider  # noqa: E402
from .tts_providers.google_provider import GoogleTTSProvider  # noqa: E402
from .tts_providers.openai_provider import OpenAITTSProvider  # noqa: E402
from .tts_providers.azure_provider import AzureTTSProvider  # noqa: E402
from .tts_providers.elevenlabs_provider import ElevenLabsProvider  # noqa: E402
from .tts_providers.deepgram_provider import DeepgramProvider  # noqa: E402
from .tts_providers.polly_provider import PollyProvider  # noqa: E402
