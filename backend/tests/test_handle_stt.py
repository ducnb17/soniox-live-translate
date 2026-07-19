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
    async def test_final_translation_is_queued_once_at_utterance_end(self):
        messages = [
            {
                "tokens": [
                    {"text": "hola", "translation_status": "original", "is_final": True},
                    {"text": "draft", "translation_status": "translation", "is_final": False},
                    {"text": "hel", "translation_status": "translation", "is_final": True},
                    {"text": "lo", "translation_status": "translation", "is_final": True},
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

        # Queue got one complete translation, then its utterance end, followed
        # by the trailing end + None sentinel from finally.
        items = []
        while not tts_queue.empty():
            items.append(tts_queue.get_nowait())

        # Non-final translation text is excluded, and all final fragments are
        # emitted as one TTS_TEXT immediately before TTS_END.
        assert items[0] == (TTS_TEXT, "hello", "vi")

        # Second: <end> token from the token stream
        assert items[1][0] == TTS_END

        # Third: trailing <end> from finally
        assert items[2][0] == TTS_END

        # Fourth: None sentinel
        assert items[3] is TTS_NONE

        # stt_done flag set
        assert tts_state["stt_done"] is True


class TestHandleSttTwoWay:
    async def test_complete_utterances_routed_by_source_language(self):
        messages = [
            {
                "tokens": [
                    {"text": "hel", "translation_status": "translation", "source_language": "en", "is_final": True},
                    {"text": "lo", "translation_status": "translation", "source_language": "en", "is_final": True},
                ]
            },
            {
                "tokens": [
                    {"text": "<end>", "source_language": "en"},
                ]
            },
            {
                "tokens": [
                    {"text": "ho", "translation_status": "translation", "source_language": "es", "is_final": True},
                    {"text": "la", "translation_status": "translation", "source_language": "es", "is_final": True},
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


class TestHandleSttLongTranslation:
    async def test_long_translation_is_flushed_before_end(self):
        long_translation = "This is a complete translated sentence. " * 6
        messages = [
            {
                "tokens": [
                    {
                        "text": long_translation,
                        "translation_status": "translation",
                        "is_final": True,
                    }
                ]
            },
            {"tokens": [{"text": "<end>"}]},
            {"finished": True},
        ]
        tts_queue: asyncio.Queue = asyncio.Queue()
        callback = AsyncMock()

        await handle_stt(
            stt_ws=FakeSttWs(messages),
            browser_ws=FakeBrowserWs(),
            tts_queue=tts_queue,
            tts_state=new_tts_state(["vi"]),
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
            on_final_segment=callback,
        )

        items = []
        while not tts_queue.empty():
            items.append(tts_queue.get_nowait())

        utterance_end = items.index((TTS_END, "vi"))
        text_items = items[:utterance_end]
        assert len(text_items) == 2
        assert all(item[0] == TTS_TEXT for item in text_items)
        assert all(item[2] == "vi" for item in text_items)
        assert len(text_items[0][1]) <= 200
        assert text_items[0][1].rstrip().endswith(".")
        assert "".join(item[1] for item in text_items) == long_translation
        assert callback.await_args.args[0]["translated_text"] == long_translation


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


class TestHandleSttFinalPersistenceCallback:
    async def test_callback_contains_only_accumulated_final_tokens(self):
        messages = [
            {
                "start_time_ms": 100,
                "tokens": [
                    {"text": "draft ", "translation_status": "original", "is_final": False},
                    {"text": "hello ", "translation_status": "original", "is_final": True},
                    {"text": "world", "translation_status": "original", "is_final": True},
                    {"text": "bản nháp", "translation_status": "translation", "is_final": False},
                    {"text": "xin chào", "translation_status": "translation", "is_final": True},
                    {"text": "<end>", "is_final": True},
                ],
                "end_time_ms": 900,
            },
            {"finished": True},
        ]
        callback = AsyncMock()

        await handle_stt(
            stt_ws=FakeSttWs(messages),
            browser_ws=FakeBrowserWs(),
            tts_queue=None,
            tts_state=new_tts_state(["vi"]),
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
            on_final_segment=callback,
        )

        callback.assert_awaited_once()
        persisted = callback.await_args.args[0]
        assert persisted["original_text"] == "hello world"
        assert persisted["translated_text"] == "xin chào"
        assert "draft" not in persisted["original_text"]
        assert "bản nháp" not in persisted["translated_text"]
