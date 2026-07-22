"""Tests for context_builder.build_stt_config + _normalize_context."""
import pytest

from app import config as runtime_config
from app.context_builder import build_stt_config, _normalize_context


class TestBuildSttConfig:
    def test_api_key_is_read_at_call_time(self, monkeypatch):
        monkeypatch.setattr(runtime_config, "SONIOX_API_KEY", "runtime-key")

        cfg = build_stt_config(
            mode="one_way",
            target_lang="vi",
            lang_a=None,
            lang_b=None,
            lang_id=True,
            diarize=False,
            context=None,
        )

        assert cfg["api_key"] == "runtime-key"

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
        assert cfg["audio_format"] == "pcm_s16le"
        assert cfg["sample_rate"] == 16000
        assert cfg["num_channels"] == 1
        assert cfg["enable_endpoint_detection"] is True
        # Soniox STS default: 500 ms end-of-utterance latency.
        assert cfg["max_endpoint_delay_ms"] == 500
        assert cfg["enable_speaker_diarization"] is True
        assert cfg["enable_language_identification"] is True
        # The target language is not the spoken input language and therefore
        # must not be used as an STT recognition hint.
        assert "language_hints" not in cfg
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
        # Without lang_id, language_hints are not auto-derived.
        assert "language_hints" not in cfg
        assert cfg["translation"] == {
            "type": "two_way",
            "language_a": "en",
            "language_b": "es",
        }

    def test_two_way_auto_language_hints(self):
        cfg = build_stt_config(
            mode="two_way",
            target_lang=None,
            lang_a="en",
            lang_b="es",
            lang_id=True,
            diarize=False,
            context=None,
        )
        # Both directions should be hinted.
        assert cfg["language_hints"] == ["en", "es"]

    def test_custom_endpoint_delay(self):
        cfg = build_stt_config(
            mode="one_way",
            target_lang="vi",
            lang_a=None,
            lang_b=None,
            lang_id=True,
            diarize=True,
            context=None,
            max_endpoint_delay_ms=2500,
        )

        assert cfg["max_endpoint_delay_ms"] == 2500

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
