import pytest

from app.translation_provider import get_available_providers, get_provider


def test_translation_registry_covers_default_and_external_engines():
    providers = get_available_providers()
    assert {provider.id for provider in providers} == {"soniox", "google", "deepl", "openai"}
    assert next(provider for provider in providers if provider.id == "soniox").supports_realtime_translation
    assert all(provider.tier in {"free", "cheap", "premium"} for provider in providers)


@pytest.mark.parametrize("provider_id", ["google", "deepl", "openai"])
async def test_external_translation_connection_requires_key(provider_id):
    provider = get_provider(provider_id)
    assert provider is not None
    ok, message = await provider.test_connection()
    assert ok is False
    assert "API key is required" in message
