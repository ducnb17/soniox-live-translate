"""Tests for context_builder.build_stt_config + _normalize_context."""
import pytest

from app.context_builder import build_stt_config, _normalize_context


class TestBuildSttConfig:
    def test_one_way_basic(self):
        cfg = build_stt_config(
            mode="one_way",
            target_lang="vi",
            lang_a=None,
            lang_b=None,
            lang_id=True,
            diarize=True,
            context=None,
        )
        assert cfg["model"] == "stt-rt-v5"
        assert cfg["audio_format"] == "auto"
        assert cfg["enable_endpoint_detection"] is True
        assert cfg["max_endpoint_delay_ms"] == 500
        assert cfg["enable_speaker_diarization"] is True
        assert cfg["enable_language_identification"] is True
        assert cfg["translation"] == {"type": "one_way", "target_language": "vi"}
        assert "context" not in cfg

    def test_two_way_basic(self):
        cfg = build_stt_config(
            mode="two_way",
            target_lang=None,
            lang_a="en",
            lang_b="es",
            lang_id=False,
            diarize=False,
            context=None,
        )
        assert cfg["enable_speaker_diarization"] is False
        assert cfg["enable_language_identification"] is False
        assert cfg["translation"] == {
            "type": "two_way",
            "language_a": "en",
            "language_b": "es",
        }

    def test_one_way_missing_target_raises(self):
        with pytest.raises(ValueError, match="target_lang"):
            build_stt_config(
                mode="one_way",
                target_lang=None,
                lang_a=None,
                lang_b=None,
                lang_id=True,
                diarize=True,
                context=None,
            )

    def test_two_way_missing_langs_raises(self):
        with pytest.raises(ValueError, match="lang_a and lang_b"):
            build_stt_config(
                mode="two_way",
                target_lang=None,
                lang_a="en",
                lang_b=None,
                lang_id=True,
                diarize=True,
                context=None,
            )

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="unknown mode"):
            build_stt_config(
                mode="three_way",
                target_lang="vi",
                lang_a=None,
                lang_b=None,
                lang_id=True,
                diarize=True,
                context=None,
            )

    def test_with_context(self):
        ctx = {
            "general": [["domain", "Healthcare"]],
            "terms": ["Celebrex"],
            "translation_terms": [["Mr. Smith", "Sr. Smith"]],
        }
        cfg = build_stt_config(
            mode="one_way",
            target_lang="es",
            lang_a=None,
            lang_b=None,
            lang_id=True,
            diarize=True,
            context=ctx,
        )
        assert cfg["context"] == {
            "general": [{"key": "domain", "value": "Healthcare"}],
            "terms": ["Celebrex"],
            "translation_terms": [{"source": "Mr. Smith", "target": "Sr. Smith"}],
        }


class TestNormalizeContext:
    def test_empty_dict(self):
        assert _normalize_context({}) == {}

    def test_text_field(self):
        out = _normalize_context({"text": "hello world"})
        assert out == {"text": "hello world"}

    def test_partial_keys(self):
        out = _normalize_context({"terms": ["A", "B"]})
        assert out == {"terms": ["A", "B"]}

    def test_all_keys(self):
        ctx = {
            "general": [["k1", "v1"], ["k2", "v2"]],
            "text": "passage",
            "terms": ["t1"],
            "translation_terms": [["src", "tgt"]],
        }
        out = _normalize_context(ctx)
        assert out["general"] == [
            {"key": "k1", "value": "v1"},
            {"key": "k2", "value": "v2"},
        ]
        assert out["text"] == "passage"
        assert out["terms"] == ["t1"]
        assert out["translation_terms"] == [{"source": "src", "target": "tgt"}]
