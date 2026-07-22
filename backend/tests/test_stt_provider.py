from unittest.mock import AsyncMock

import pytest

from app.stt_provider import (
    STTProviderInfo,
    get_available_providers,
    get_provider,
)


EXPECTED_PROVIDERS = {
    "soniox",
    "openai",
    "deepgram",
    "google_v2",
    "assemblyai",
}


def test_all_stt_providers_are_registered_with_explicit_capabilities():
    infos = get_available_providers()

    assert {info.id for info in infos} == EXPECTED_PROVIDERS
    assert {info.tier for info in infos} <= {"free", "cheap", "premium"}
    assert all(info.pricing_url.startswith("https://") for info in infos)
    assert all(info.approximate_cost_per_hour >= 0 for info in infos)

    whisper = next(info for info in infos if info.id == "openai")
    assert whisper.supports_streaming is False

    realtime_translators = [
        info.id for info in infos if info.supports_realtime_translation
    ]
    assert realtime_translators == ["soniox"]


def test_stt_provider_tier_rejects_unknown_badge():
    with pytest.raises(ValueError, match="tier must be one of"):
        STTProviderInfo(
            id="invalid",
            name="Invalid",
            description="Invalid tier",
            requires_api_key=True,
            supports_streaming=False,
            supports_realtime_translation=False,
            tier="expensive",
            pricing_url="https://example.com/pricing",
        )


@pytest.mark.parametrize("provider_id", sorted(EXPECTED_PROVIDERS - {"soniox", "google_v2"}))
async def test_external_provider_connection_check_rejects_missing_key(provider_id):
    provider = get_provider(provider_id)

    assert provider is not None
    ok, message = await provider.test_connection()

    assert ok is False
    assert "API key is required" in message


async def test_google_v2_provider_rejects_missing_credentials():
    provider = get_provider("google_v2")

    assert provider is not None
    ok, message = await provider.test_connection()

    assert ok is False
    assert "credentials required" in message.lower()


@pytest.mark.parametrize(
    ("provider_id", "module_name"),
    [
        ("soniox", "soniox_provider"),
        ("openai", "openai_provider"),
        ("deepgram", "deepgram_provider"),
        ("assemblyai", "assemblyai_provider"),
    ],
)
async def test_each_provider_implements_a_lightweight_connection_check(
    monkeypatch,
    provider_id,
    module_name,
):
    import importlib

    provider_module = importlib.import_module(f"app.stt_providers.{module_name}")
    request = AsyncMock(return_value=(True, "OK"))
    monkeypatch.setattr(provider_module, "test_get", request)
    provider = get_provider(provider_id, api_key="test-key")

    assert provider is not None
    assert await provider.test_connection() == (True, "OK")
    request.assert_awaited_once()


async def test_non_streaming_provider_raises_not_implemented():
    provider = get_provider("deepgram", api_key="test-key")

    async def audio():
        yield b"audio"

    assert provider is not None
    with pytest.raises(NotImplementedError, match="not connected"):
        await anext(provider.transcribe_stream(audio()))


# ── Google V2 provider unit tests ──


class TestGoogleV2Provider:
    def test_info_has_correct_shape(self):
        provider = get_provider("google_v2")
        assert provider is not None
        info = provider.info
        assert info.id == "google_v2"
        assert "Chirp 3" in info.name
        assert info.supports_streaming is True
        assert info.supports_realtime_translation is False
        assert info.tier == "premium"
        assert "speech-to-text/pricing" in info.pricing_url

    def test_credentials_not_found_when_empty(self):
        provider = get_provider("google_v2", api_key="")
        assert provider is not None
        from app.stt_providers.google_provider import _parse_credentials
        assert _parse_credentials(None) is None
        assert _parse_credentials("") is None

    def test_parse_credentials_returns_raw_json(self):
        from app.stt_providers.google_provider import _parse_credentials
        creds = '{"project_id": "my-project", "client_email": "x@y.com"}'
        assert _parse_credentials(creds) == creds


class TestGoogleSttRouting:
    """Two-way direction routing using `language` field per STS docs."""

    def test_language_field_on_token_directly_routes(self, monkeypatch):
        """When a token has `language`, use it as the TTS target."""
        from app.stt import _resolve_tts_target
        result = _resolve_tts_target(
            {"language": "es"}, "two_way", "en", "es", None
        )
        assert result == "es"

    def test_source_language_fallback_still_works(self):
        from app.stt import _resolve_tts_target
        result = _resolve_tts_target(
            {"source_language": "en"}, "two_way", "en", "es", None
        )
        assert result == "es"

    def test_missing_both_fields_falls_back_to_target_lang(self):
        from app.stt import _resolve_tts_target
        result = _resolve_tts_target({}, "two_way", "en", "es", "vi")
        assert result == "vi"

    def test_language_field_on_end_direction(self):
        from app.stt import _direction
        result = _direction({"language": "es"}, "two_way", "en", "es", source_lang=None)
        assert result == "es"

    def test_end_direction_falls_back_to_tracked_source(self):
        from app.stt import _direction
        result = _direction({}, "two_way", "en", "es", source_lang="en")
        assert result == "es"
