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
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "api_key": runtime_config.SONIOX_API_KEY,
        "model": STT_MODEL,
        "audio_format": "auto",
        "enable_endpoint_detection": True,
        "max_endpoint_delay_ms": max_endpoint_delay_ms,
        "enable_speaker_diarization": diarize,
        "enable_language_identification": lang_id,
    }

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
