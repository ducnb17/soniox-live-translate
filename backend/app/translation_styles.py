"""Shared translation-style definitions and prompt instructions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationStyle:
    id: str
    name: str
    instruction: str


TRANSLATION_STYLES = (
    TranslationStyle(
        "natural",
        "Natural",
        "Use fluent, idiomatic phrasing while preserving the original meaning and tone.",
    ),
    TranslationStyle(
        "literal",
        "Literal",
        "Stay as close as possible to the original wording and structure without omitting details.",
    ),
    TranslationStyle(
        "professional",
        "Professional",
        "Use polished, professional language suitable for business communication.",
    ),
    TranslationStyle(
        "casual",
        "Casual",
        "Use natural, friendly, conversational language without adding unsupported slang.",
    ),
    TranslationStyle(
        "subtitle_game",
        "Subtitle / Game",
        "Use concise subtitle or game-localization phrasing, preserving names, intent, and key details.",
    ),
    TranslationStyle(
        "technical",
        "Technical",
        "Use precise technical terminology and preserve code, commands, identifiers, units, and product names.",
    ),
)

TRANSLATION_STYLE_IDS = tuple(style.id for style in TRANSLATION_STYLES)
_STYLE_BY_ID = {style.id: style for style in TRANSLATION_STYLES}


def normalize_translation_style(value: str | None) -> str:
    style_id = (value or "natural").strip().lower()
    if style_id not in _STYLE_BY_ID:
        allowed = ", ".join(TRANSLATION_STYLE_IDS)
        raise ValueError(f"translation_style must be one of: {allowed}")
    return style_id


def translation_style_instruction(value: str | None) -> str:
    return _STYLE_BY_ID[normalize_translation_style(value)].instruction
