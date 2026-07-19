"""TTS provider abstraction layer.

Each provider implements the TTSProviderBase interface. The factory
`get_provider()` returns an instance by provider ID. New providers
can be added by implementing the interface without touching the UI.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator
import os

from .logging_config import get_logger

log = get_logger("tts_provider")


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
    pricing_url: str
    approximate_cost_per_1m_chars: float = 0.0
    provider_class: str = ""


class TTSProviderBase(ABC):
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


# ── Cache ──

class TTSCache:
    """Bounded in-memory LRU cache for TTS audio by text+voice+provider hash."""

    def __init__(self, max_size: int = 100, max_bytes: int = 64 * 1024 * 1024) -> None:
        if max_size < 1 or max_bytes < 1:
            raise ValueError("TTS cache limits must be positive")
        self._cache: dict[str, bytes] = {}
        self._order: list[str] = []
        self._max_size = max_size
        self._max_bytes = max_bytes
        self._total_bytes = 0

    def _key(self, text: str, voice_id: str, provider_id: str) -> str:
        import hashlib
        return hashlib.sha256(f"{text}|{voice_id}|{provider_id}".encode()).hexdigest()

    def get(self, text: str, voice_id: str, provider_id: str) -> bytes | None:
        key = self._key(text, voice_id, provider_id)
        value = self._cache.get(key)
        if value is not None:
            self._order.remove(key)
            self._order.append(key)
        return value

    def set(self, text: str, voice_id: str, provider_id: str, data: bytes) -> None:
        k = self._key(text, voice_id, provider_id)
        if k in self._cache:
            self._order.remove(k)
            self._total_bytes -= len(self._cache[k])
        if len(data) > self._max_bytes:
            self._cache.pop(k, None)
            return
        while self._order and (
            len(self._cache) >= self._max_size
            or self._total_bytes + len(data) > self._max_bytes
        ):
            oldest = self._order.pop(0)
            self._total_bytes -= len(self._cache.pop(oldest))
        self._cache[k] = data
        self._order.append(k)
        self._total_bytes += len(data)

    def clear(self) -> None:
        self._cache.clear()
        self._order.clear()
        self._total_bytes = 0

    @property
    def entry_count(self) -> int:
        return len(self._cache)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes


tts_cache = TTSCache(
    max_size=int(os.environ.get("TTS_CACHE_MAX_ENTRIES", "100")),
    max_bytes=int(os.environ.get("TTS_CACHE_MAX_MB", "64")) * 1024 * 1024,
)


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
