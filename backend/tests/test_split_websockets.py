from unittest.mock import AsyncMock
import json

import pytest

from app import main
from app.stt import handle_stt

pytestmark = pytest.mark.asyncio


async def test_stt_route_forces_legacy_adapter_into_stt_only_mode(monkeypatch):
    adapter = AsyncMock()
    monkeypatch.setattr(main, "translation_websocket", adapter)
    browser = object()

    await main.stt_websocket(browser_ws=browser, target_lang="vi")

    kwargs = adapter.await_args.kwargs
    assert kwargs["browser_ws"] is browser
    assert kwargs["target_lang"] == "vi"
    assert kwargs["tts"] is False
    assert "tts_provider" not in kwargs


async def test_tts_route_delegates_to_independent_session(monkeypatch):
    serve = AsyncMock()
    monkeypatch.setattr(main, "serve_tts_websocket", serve)
    browser = object()

    await main.tts_websocket(browser)

    serve.assert_awaited_once_with(browser)


async def test_stt_core_emits_lines_without_any_tts_queue():
    class Stt:
        def __init__(self):
            self.messages = [
                {"tokens": [
                    {"text": "xin chao", "is_final": True},
                    {"text": "hello", "translation_status": "translation", "is_final": True},
                    {"text": "<end>"},
                ]},
                {"tokens": [], "finished": True},
            ]

        async def recv(self):
            return json.dumps(self.messages.pop(0))

    class Browser:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

    browser = Browser()
    await handle_stt(
        stt_ws=Stt(),
        browser_ws=browser,
        session_state={"line_counter": 0},
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
    )

    line = next(message for message in browser.messages if message.get("type") == "line_ready")
    assert line["translated_text"] == "hello"
    assert line["target_lang"] == "vi"
    assert line["direction"] == "vi"
    assert browser.messages[-1] == {"session_done": True}
