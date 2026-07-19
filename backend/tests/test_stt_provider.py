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
    "google",
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


@pytest.mark.parametrize("provider_id", sorted(EXPECTED_PROVIDERS - {"soniox"}))
async def test_external_provider_connection_check_rejects_missing_key(provider_id):
    provider = get_provider(provider_id)

    assert provider is not None
    ok, message = await provider.test_connection()

    assert ok is False
    assert "API key is required" in message


@pytest.mark.parametrize(
    ("provider_id", "module_name"),
    [
        ("soniox", "soniox_provider"),
        ("openai", "openai_provider"),
        ("deepgram", "deepgram_provider"),
        ("google", "google_provider"),
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


async def test_registry_does_not_fake_streaming_by_chunking_audio():
    provider = get_provider("deepgram", api_key="test-key")

    async def audio():
        yield b"audio"

    assert provider is not None
    with pytest.raises(NotImplementedError, match="not connected"):
        await anext(provider.transcribe_stream(audio()))
