"""STT provider registry and shared provider metadata.

This mirrors :mod:`app.tts_provider` while keeping the existing Soniox
WebSocket pipeline in ``stt.py`` and ``context_builder.py`` untouched.
Providers registered here describe capabilities for selection/configuration;
the active audio pipeline can adopt them independently.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


VALID_PROVIDER_TIERS = frozenset({"free", "cheap", "premium"})


@dataclass
class STTProviderInfo:
    id: str
    name: str
    description: str
    requires_api_key: bool
    supports_streaming: bool
    supports_realtime_translation: bool
    tier: str
    pricing_url: str
    approximate_cost_per_hour: float = 0.0
    provider_class: str = ""

    def __post_init__(self) -> None:
        if self.tier not in VALID_PROVIDER_TIERS:
            allowed = ", ".join(sorted(VALID_PROVIDER_TIERS))
            raise ValueError(f"tier must be one of: {allowed}")


class STTProviderBase(ABC):
    """Common interface for STT provider metadata and connection checks.

    ``transcribe_stream`` deliberately has no fake chunked-audio fallback.
    A provider adapter must override it only when it integrates the vendor's
    genuine streaming transport.
    """

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        **options: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(
            f"{self.info.name} streaming adapter is not connected to the active pipeline"
        )
        if False:  # pragma: no cover - keeps this method an async generator
            yield {}

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """Validate credentials with the provider's lightest practical call."""
        ...

    @property
    @abstractmethod
    def info(self) -> STTProviderInfo:
        ...


_provider_registry: dict[str, type[STTProviderBase]] = {}


def register_provider(provider_cls: type[STTProviderBase]) -> type[STTProviderBase]:
    inst = provider_cls(api_key=None)
    _provider_registry[inst.info.id] = provider_cls
    return provider_cls


def get_provider(
    provider_id: str,
    api_key: str | None = None,
) -> STTProviderBase | None:
    cls = _provider_registry.get(provider_id)
    if cls is None:
        return None
    return cls(api_key=api_key)


def get_available_providers() -> list[STTProviderInfo]:
    result = []
    for cls in _provider_registry.values():
        inst = cls(api_key=None)
        result.append(inst.info)
    return result


# Import and register all providers.
from .stt_providers.soniox_provider import SonioxSTTProvider  # noqa: E402
from .stt_providers.openai_provider import OpenAIWhisperProvider  # noqa: E402
from .stt_providers.deepgram_provider import DeepgramSTTProvider  # noqa: E402
from .stt_providers.google_provider import GoogleSTTProvider  # noqa: E402
from .stt_providers.assemblyai_provider import AssemblyAISTTProvider  # noqa: E402
