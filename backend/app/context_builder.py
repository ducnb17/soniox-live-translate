"""Build the Soniox STT (with translation) session config from client request.

Context is optional and supports `general`, `terms` and `translation_terms`
keys — see https://soniox.com/docs/stt/concepts/context.
"""

from typing import Any

from . import config as runtime_config
from .config import MAX_ENDPOINT_DELAY_MS, STT_MODEL


def build_stt_config(
    *,
    mode: str,
    target_lang: str | None,
    lang_a: str | None,
    lang_b: str | None,
    lang_id: bool,
    diarize: bool,
    context: dict[str, Any] | None,
    max_endpoint_delay_ms: int = MAX_ENDPOINT_DELAY_MS,
    enable_translation: bool = True,
    language_hints: list[str] | None = None,
) -> dict[str, Any]:
    """Build the real-time STT+translation session config.

    See ``https://soniox.com/docs/translation/stt-translation/rt-translation``
    for the official configuration shape. ``language_hints`` lets the model
    bias recognition toward expected languages, and a low
    ``max_endpoint_delay_ms`` (Soniox STS example uses 500 ms) keeps the
    real-time speech-to-speech pipeline's end-to-end latency minimal.
    """
    config: dict[str, Any] = {
        "api_key": runtime_config.SONIOX_API_KEY,
        "model": STT_MODEL,
        "audio_format": "pcm_s16le",
        "sample_rate": 16000,
        "num_channels": 1,
        "enable_endpoint_detection": True,
        "max_endpoint_delay_ms": max_endpoint_delay_ms,
        "enable_speaker_diarization": diarize,
        "enable_language_identification": lang_id,
    }

    # Soniox strongly recommends setting language_hints when known — it
    # "significantly improves accuracy" per the real-time STT docs.
    # Push default hints from the conversation pair when the caller
    # didn't supply explicit ones.
    if language_hints is None and lang_id:
        if mode == "two_way":
            language_hints = [l for l in (lang_a, lang_b) if l]
        # In one-way translation the configured language is the *target*,
        # not necessarily an input language. Hinting it can bias recognition
        # away from the speaker, so leave source detection unconstrained.
    if language_hints:
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique_hints: list[str] = []
        for hint in language_hints:
            if hint and hint not in seen:
                seen.add(hint)
                unique_hints.append(hint)
        if unique_hints:
            config["language_hints"] = unique_hints

    if context:
        config["context"] = _normalize_context(context)
    if not enable_translation:
        return config
    if mode == "one_way":
        if not target_lang:
            raise ValueError("one_way mode requires target_lang")
        config["translation"] = {"type": "one_way", "target_language": target_lang}
    elif mode == "two_way":
        if not lang_a or not lang_b:
            raise ValueError("two_way mode requires lang_a and lang_b")
        config["translation"] = {"type": "two_way", "language_a": lang_a, "language_b": lang_b}
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    return config


def _normalize_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Accept a compact client-side shape and expand it to the Soniox schema.

    Expected compact shape:
        {
          "general": [[key, value], ...],
          "text": "...",
          "terms": ["..."],
          "translation_terms": [[source, target], ...]
        }
    """
    out: dict[str, Any] = {}
    if "general" in ctx:
        out["general"] = [
            {"key": k, "value": v} for k, v in ctx["general"]
        ]
    if "text" in ctx:
        out["text"] = ctx["text"]
    if "terms" in ctx:
        out["terms"] = list(ctx["terms"])
    if "translation_terms" in ctx:
        out["translation_terms"] = [
            {"source": s, "target": t} for s, t in ctx["translation_terms"]
        ]
    return out
