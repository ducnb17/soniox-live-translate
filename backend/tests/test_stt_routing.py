"""Tests for STT translation direction routing.

These are the pure functions that decide which TTS direction a translation
token or <end> token belongs to. They're the heart of the two-way routing.
"""
import pytest

from app.stt import _direction, _resolve_translation_target


class TestResolveTtsTarget:
    def test_one_way_returns_target_lang(self):
        token = {"source_language": "en"}
        assert _resolve_translation_target(token, "one_way", None, None, "vi") == "vi"

    def test_two_way_speaker_a_to_b(self):
        # Speaker A speaks lang_a (en) → translate to lang_b (es)
        token = {"source_language": "en"}
        assert _resolve_translation_target(token, "two_way", "en", "es", None) == "es"

    def test_two_way_speaker_b_to_a(self):
        # Speaker B speaks lang_b (es) → translate to lang_a (en)
        token = {"source_language": "es"}
        assert _resolve_translation_target(token, "two_way", "en", "es", None) == "en"

    def test_two_way_unknown_source_falls_back(self):
        token = {"source_language": "fr"}
        assert _resolve_translation_target(token, "two_way", "en", "es", "vi") == "vi"

    def test_two_way_missing_source_language(self):
        token = {}
        assert _resolve_translation_target(token, "two_way", "en", "es", "vi") == "vi"


class TestDirection:
    def test_one_way_returns_none(self):
        token = {"source_language": "en"}
        assert _direction(token, "one_way", None, None) is None

    def test_two_way_speaker_a(self):
        token = {"source_language": "en"}
        assert _direction(token, "two_way", "en", "es") == "es"

    def test_two_way_speaker_b(self):
        token = {"source_language": "es"}
        assert _direction(token, "two_way", "en", "es") == "en"

    def test_two_way_unknown_source(self):
        token = {"source_language": "fr"}
        assert _direction(token, "two_way", "en", "es") is None

    def test_two_way_missing_source_language(self):
        token = {}
        assert _direction(token, "two_way", "en", "es") is None
