"""Pocket TTS provider (kyutai-labs/pocket-tts).

Runs a small (100M param) TTS model locally on CPU. No API key, no network
calls at synthesis time (only the first model/voice download from Hugging
Face). Only supports English, French, German, Italian, Portuguese, Spanish —
callers should fall back to another provider for unsupported languages.
"""

import asyncio
import threading
from typing import AsyncIterator

import numpy as np

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger

log = get_logger("pocket_tts")

# lang code (as used elsewhere in this app) -> pocket-tts language preset.
# French only ships as the larger 24-layer variant (plain "french" is rejected
# by pocket-tts); the others have a base config available.
LANGUAGE_MAP: dict[str, str] = {
    "en": "english",
    "fr": "french_24l",
    "de": "german",
    "it": "italian",
    "pt": "portuguese",
    "es": "spanish",
}

VOICES_BY_LANG: dict[str, list[str]] = {
    "en": [
        "alba", "anna", "azelma", "bill_boerst", "caro_davy", "charles",
        "cosette", "eponine", "eve", "fantine", "george", "jane", "jean",
        "javert", "marius", "mary", "michael", "paul", "peter_yearsley",
        "stuart_bell", "vera",
    ],
    "fr": ["estelle"],
    "de": ["juergen"],
    "it": ["giovanni"],
    "pt": ["rafael"],
    "es": ["lola"],
}

# Cache of loaded TTSModel instances per pocket-tts language preset, plus a
# lock per model since thread-safety of concurrent generation isn't documented.
_MODEL_CACHE: dict[str, tuple[object, threading.Lock]] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _load_model_sync(language: str):
    from pocket_tts import TTSModel

    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(language)
        if cached is not None:
            return cached
        model = TTSModel.load_model(language=language)
        entry = (model, threading.Lock())
        _MODEL_CACHE[language] = entry
        return entry


def _generate_sync(language: str, voice_id: str, text: str) -> bytes:
    model, lock = _load_model_sync(language)
    with lock:
        state = model.get_state_for_audio_prompt(voice_id)
        chunks = [chunk.numpy() for chunk in model.generate_audio_stream(state, text)]
    if not chunks:
        return b""
    audio = np.concatenate(chunks)
    pcm = np.clip(audio, -1.0, 1.0)
    return (pcm * 32767.0).astype(np.int16).tobytes()


@register_provider
class PocketTTSProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        try:
            import pocket_tts  # noqa: F401
        except ImportError as exc:
            return False, f"pocket-tts is not installed: {exc}"
        return True, "OK"

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        names = VOICES_BY_LANG.get(lang or "", [])
        return [
            Voice(
                id=name,
                name=name.replace("_", " ").title(),
                language=lang or "en",
                gender="neutral",
                provider_id="pocket_tts",
            )
            for name in names
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        language = LANGUAGE_MAP.get(lang)
        if language is None:
            raise ValueError(f"pocket-tts does not support language '{lang}'")

        loop = asyncio.get_running_loop()
        audio = await loop.run_in_executor(None, _generate_sync, language, voice_id, text)
        if audio:
            yield audio

    def estimate_cost(self, char_count: int) -> float:
        return 0.0

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="pocket_tts",
            name="Pocket TTS (local, offline)",
            description=(
                "Runs locally on CPU, no API key needed. Supports English, French, "
                "German, Italian, Portuguese, Spanish only."
            ),
            requires_api_key=False,
            supports_streaming=False,
            tier="free",
            pricing_url="",
            approximate_cost_per_1m_chars=0.0,
        )
