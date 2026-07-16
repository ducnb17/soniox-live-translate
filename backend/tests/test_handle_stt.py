"""Integration test for handle_stt: mock the STT WebSocket, verify that
translation tokens and <end> tokens are routed to the TTS queue correctly
for both one_way and two_way modes."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.stt import handle_stt, TTS_TEXT, TTS_END, TTS_NONE
from app.tts import new_tts_state


class FakeSttWs:
    """Yields pre-loaded JSON messages, then closes."""
    def __init__(self, messages: list[dict]):
        self._messages = list(messages)

    async def recv(self):
        if not self._messages:
            raise ConnectionResetError("done")
        return json.dumps(self._messages.pop(0))


class FakeBrowserWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data):
        self.sent.append(data)


class TestHandleSttOneWay:
    async def test_translation_tokens_routed_to_queue(self):
        messages = [
            {
                "tokens": [
                    {"text": "hola", "translation_status": "original", "is_final": True},
                    {"text": "hello", "translation_status": "translation"},
                ]
            },
            {
                "tokens": [
                    {"text": "<end>"},
                ]
            },
            {"finished": True},
        ]
        stt_ws = FakeSttWs(messages)
        browser_ws = FakeBrowserWs()
        tts_queue: asyncio.Queue = asyncio.Queue()
        tts_state = new_tts_state(["vi"])
        tts_state["stt_done"] = False

        await handle_stt(
            stt_ws=stt_ws,
            browser_ws=browser_ws,
            tts_queue=tts_queue,
            tts_state=tts_state,
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
        )

        # All STT JSON was forwarded to browser
        assert len(browser_ws.sent) == 3

        # Queue got: text "hello", end (with direction None for one_way),
        # then the trailing end + None sentinel from finally.
        items = []
        while not tts_queue.empty():
            items.append(tts_queue.get_nowait())

        # First: text "hello" → target "vi"
        assert items[0][0] == TTS_TEXT
        assert items[0][1] == "hello"
        assert items[0][2] == "vi"

        # Second: <end> token from the token stream
        assert items[1][0] == TTS_END

        # Third: trailing <end> from finally
        assert items[2][0] == TTS_END

        # Fourth: None sentinel
        assert items[3] is TTS_NONE

        # stt_done flag set
        assert tts_state["stt_done"] is True


class TestHandleSttTwoWay:
    async def test_tokens_routed_by_source_language(self):
        messages = [
            {
                "tokens": [
                    {"text": "hello", "translation_status": "translation", "source_language": "en"},
                ]
            },
            {
                "tokens": [
                    {"text": "<end>", "source_language": "en"},
                ]
            },
            {
                "tokens": [
                    {"text": "hola", "translation_status": "translation", "source_language": "es"},
                ]
            },
            {
                "tokens": [
                    {"text": "<end>", "source_language": "es"},
                ]
            },
            {"finished": True},
        ]
        stt_ws = FakeSttWs(messages)
        browser_ws = FakeBrowserWs()
        tts_queue: asyncio.Queue = asyncio.Queue()
        tts_state = new_tts_state(["en", "es"])

        await handle_stt(
            stt_ws=stt_ws,
            browser_ws=browser_ws,
            tts_queue=tts_queue,
            tts_state=tts_state,
            mode="two_way",
            lang_a="en",
            lang_b="es",
            target_lang=None,
        )

        items = []
        while not tts_queue.empty():
            items.append(tts_queue.get_nowait())

        # "hello" from en speaker → translate to es
        assert items[0] == (TTS_TEXT, "hello", "es")
        # <end> from en speaker → direction es
        assert items[1] == (TTS_END, "es")
        # "hola" from es speaker → translate to en
        assert items[2] == (TTS_TEXT, "hola", "en")
        # <end> from es speaker → direction en
        assert items[3] == (TTS_END, "en")
        # Trailing <end> (direction=None) + None sentinel from finally
        assert items[4] == (TTS_END, None)
        assert items[5] is TTS_NONE


class TestHandleSttErrorForwarded:
    async def test_error_code_breaks_loop(self):
        messages = [
            {"error_code": "bad_audio", "error_message": "format not supported"},
        ]
        stt_ws = FakeSttWs(messages)
        browser_ws = FakeBrowserWs()
        tts_queue: asyncio.Queue = asyncio.Queue()
        tts_state = new_tts_state(["vi"])

        await handle_stt(
            stt_ws=stt_ws,
            browser_ws=browser_ws,
            tts_queue=tts_queue,
            tts_state=tts_state,
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
        )

        # Error was forwarded to browser
        assert browser_ws.sent[0]["error_code"] == "bad_audio"
        # stt_done was set
        assert tts_state["stt_done"] is True
