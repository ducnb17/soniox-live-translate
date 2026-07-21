"""Integration test for handle_stt: mock the STT WebSocket, verify that
translation tokens and <end> tokens are routed to the TTS queue correctly
for both one_way and two_way modes."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.stt import (
    LINE_MAX_CHARS,
    TTS_END,
    TTS_NONE,
    TTS_TEXT,
    _split_line_short,
    handle_stt,
)
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
        assert len(browser_ws.sent) == 4
        assert browser_ws.sent[2] == {
            "type": "line_ready",
            "line_id": 1,
            "speaker": None,
            "original_text": "hola",
            "translated_text": "hello",
            "lang": None,
            "is_endpoint": True,
        }

        # Queue got one complete translation, then its utterance end, followed
        # by the trailing end + None sentinel from finally.
        items = []
        while not tts_queue.empty():
            items.append(tts_queue.get_nowait())

        # Non-final translation text is excluded, and all final fragments are
        # emitted as one TTS_TEXT immediately before TTS_END.
        assert items[0] == (TTS_TEXT, "hello", "vi", 1)

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
        assert items[0] == (TTS_TEXT, "hello", "es", 1)
        # <end> from en speaker → direction es
        assert items[1] == (TTS_END, "es")
        # "hola" from es speaker → translate to en
        assert items[2] == (TTS_TEXT, "hola", "en", 2)
        # <end> from es speaker → direction en
        assert items[3] == (TTS_END, "en")
        # Trailing <end> (direction=None) + None sentinel from finally
        assert items[4] == (TTS_END, None)
        assert items[5] is TTS_NONE


async def test_line_ids_increase_across_consecutive_utterances():
    messages = [
        {
            "tokens": [
                {"text": translated, "translation_status": "translation", "is_final": True},
                {"text": "<end>"},
            ]
        }
        for translated in ("dòng một", "dòng hai", "dòng ba")
    ]
    messages.append({"finished": True})
    browser = FakeBrowserWs()
    queue: asyncio.Queue = asyncio.Queue()

    await handle_stt(
        stt_ws=FakeSttWs(messages),
        browser_ws=browser,
        tts_queue=queue,
        tts_state=new_tts_state(["vi"]),
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
    )

    ready_lines = [item for item in browser.sent if item.get("type") == "line_ready"]
    queued_lines = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, tuple) and item[0] == TTS_TEXT:
            queued_lines.append(item)

    assert [line["line_id"] for line in ready_lines] == [1, 2, 3]
    assert [item[3] for item in queued_lines] == [1, 2, 3]
    assert [item[1] for item in queued_lines] == ["dòng một", "dòng hai", "dòng ba"]


async def test_line_ids_continue_when_handle_stt_reconnects_with_session_state():
    state = new_tts_state(["vi"])
    observed_ids = []
    for translated in ("trước reconnect", "sau reconnect"):
        browser = FakeBrowserWs()
        await handle_stt(
            stt_ws=FakeSttWs([
                {
                    "tokens": [
                        {
                            "text": translated,
                            "translation_status": "translation",
                            "is_final": True,
                        },
                        {"text": "<end>"},
                    ]
                },
                {"finished": True},
            ]),
            browser_ws=browser,
            tts_queue=None,
            tts_state=state,
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
        )
        observed_ids.extend(
            item["line_id"]
            for item in browser.sent
            if item.get("type") == "line_ready"
        )

    assert observed_ids == [1, 2]
    assert state["line_counter"] == 2


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
        assert len(text_items) > 1
        assert all(item[0] == TTS_TEXT for item in text_items)
        assert all(item[2] == "vi" for item in text_items)
        assert [item[3] for item in text_items] == list(
            range(1, len(text_items) + 1)
        )
        assert all(len(item[1]) <= LINE_MAX_CHARS for item in text_items)
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


class TestHandleSttExtraHold:
    async def test_held_utterances_commit_after_delay_in_fifo_order(self, monkeypatch):
        messages = [
            {
                "tokens": [
                    {"text": "A", "translation_status": "translation", "is_final": True},
                    {"text": "<end>"},
                ]
            },
            {
                "tokens": [
                    {"text": "next interim", "translation_status": "original", "is_final": False},
                ]
            },
            {
                "tokens": [
                    {"text": "B", "translation_status": "translation", "is_final": True},
                    {"text": "<end>"},
                ]
            },
            {"finished": True},
        ]
        browser_ws = FakeBrowserWs()
        tts_queue: asyncio.Queue = asyncio.Queue()
        committed_translations: list[str] = []
        sleep_delays: list[float] = []
        real_sleep = asyncio.sleep

        async def record_sleep(delay: float) -> None:
            sleep_delays.append(delay)
            await real_sleep(0)

        async def on_final_segment(segment: dict) -> None:
            committed_translations.append(segment["translated_text"])

        monkeypatch.setattr(asyncio, "sleep", record_sleep)

        await handle_stt(
            stt_ws=FakeSttWs(messages),
            browser_ws=browser_ws,
            tts_queue=tts_queue,
            tts_state=new_tts_state(["vi"]),
            mode="one_way",
            lang_a=None,
            lang_b=None,
            target_lang="vi",
            on_final_segment=on_final_segment,
            extra_hold_ms=4000,
        )

        items = []
        while not tts_queue.empty():
            items.append(tts_queue.get_nowait())

        assert len(browser_ws.sent) == 6
        assert [message["translated_text"] for message in browser_ws.sent if message.get("type") == "line_ready"] == ["A", "B"]
        assert committed_translations == ["A", "B"]
        assert sleep_delays == [
            pytest.approx(4.0, abs=0.05),
            pytest.approx(4.0, abs=0.05),
        ]
        assert items == [
            (TTS_TEXT, "A", "vi", 1),
            (TTS_END, "vi"),
            (TTS_TEXT, "B", "vi", 2),
            (TTS_END, "vi"),
            (TTS_END, None),
            TTS_NONE,
        ]


async def test_tts_subscription_skips_disabled_lines_without_replay():
    state = new_tts_state(["vi"])
    state["enabled"] = False

    disabled_browser = FakeBrowserWs()
    disabled_queue: asyncio.Queue = asyncio.Queue()
    await handle_stt(
        stt_ws=FakeSttWs([
            {
                "tokens": [
                    {"text": "câu đã bỏ lỡ", "translation_status": "translation", "is_final": True},
                    {"text": "<end>"},
                ]
            },
            {"finished": True},
        ]),
        browser_ws=disabled_browser,
        tts_queue=disabled_queue,
        tts_state=state,
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
    )

    disabled_items = []
    while not disabled_queue.empty():
        disabled_items.append(disabled_queue.get_nowait())
    assert not any(
        isinstance(item, tuple) and item[0] == TTS_TEXT
        for item in disabled_items
    )
    assert [
        item["translated_text"]
        for item in disabled_browser.sent
        if item.get("type") == "line_ready"
    ] == ["câu đã bỏ lỡ"]

    state["enabled"] = True
    enabled_browser = FakeBrowserWs()
    enabled_queue: asyncio.Queue = asyncio.Queue()
    await handle_stt(
        stt_ws=FakeSttWs([
            {
                "tokens": [
                    {"text": "câu mới", "translation_status": "translation", "is_final": True},
                    {"text": "<end>"},
                ]
            },
            {"finished": True},
        ]),
        browser_ws=enabled_browser,
        tts_queue=enabled_queue,
        tts_state=state,
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
    )

    enabled_items = []
    while not enabled_queue.empty():
        enabled_items.append(enabled_queue.get_nowait())
    spoken = [
        item[1]
        for item in enabled_items
        if isinstance(item, tuple) and item[0] == TTS_TEXT
    ]
    assert spoken == ["câu mới"]
    ready_lines = [
        item for item in enabled_browser.sent if item.get("type") == "line_ready"
    ]
    assert [item["line_id"] for item in ready_lines] == [2]


async def test_external_translation_never_queues_original_text():
    messages = [
        {
            "tokens": [
                {"text": "Hello world.", "translation_status": "original", "is_final": True, "language": "en"},
                {"text": "<end>"},
            ]
        },
        {"finished": True},
    ]
    queue: asyncio.Queue = asyncio.Queue()

    async def translate(text: str, source: str | None, target: str) -> str:
        assert (text, source, target) == ("Hello world.", "en", "vi")
        return "Xin chào thế giới."

    await handle_stt(
        stt_ws=FakeSttWs(messages),
        browser_ws=FakeBrowserWs(),
        tts_queue=queue,
        tts_state=new_tts_state(["vi"]),
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
        translate_text=translate,
    )

    queued = []
    while not queue.empty():
        queued.append(queue.get_nowait())
    spoken = [item[1] for item in queued if isinstance(item, tuple) and item[0] == TTS_TEXT]
    assert spoken == ["Xin chào thế giới."]
    assert all("Hello world" not in text for text in spoken)


async def test_natural_endpoint_splits_long_utterance_into_short_tts_lines():
    long_translation = "Một câu hoàn chỉnh; " * 18
    messages = [
        {
            "tokens": [
                {"text": "A complete thought.", "translation_status": "original", "is_final": True},
                {"text": long_translation, "translation_status": "translation", "is_final": True},
                {"text": "<end>"},
            ]
        },
        {"finished": True},
    ]
    queue: asyncio.Queue = asyncio.Queue()
    browser = FakeBrowserWs()

    await handle_stt(
        stt_ws=FakeSttWs(messages),
        browser_ws=browser,
        tts_queue=queue,
        tts_state=new_tts_state(["vi"]),
        mode="one_way",
        lang_a=None,
        lang_b=None,
        target_lang="vi",
    )

    spoken = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, tuple) and item[0] == TTS_TEXT:
            spoken.append(item[1])
    lines = [message for message in browser.sent if message.get("type") == "line_ready"]
    assert len(spoken) > 1
    assert "".join(spoken) == long_translation
    assert all(len(part) <= LINE_MAX_CHARS for part in spoken)
    assert [line["translated_text"] for line in lines] == spoken
    assert [line["line_id"] for line in lines] == list(range(1, len(lines) + 1))
    assert [line["is_endpoint"] for line in lines[:-1]] == [False] * (len(lines) - 1)
    assert lines[-1]["is_endpoint"] is True


def test_split_line_short_is_lossless_and_prefers_sentence_then_comma_boundaries():
    text = (
        "First clause is deliberately short; second clause keeps going, "
        "with another comma, and enough whole words to exceed the cap.\n"
        "Final thought… Follow-up."
    )

    chunks = _split_line_short(text, 45)

    assert "".join(chunks) == text
    assert all(len(chunk) <= 45 for chunk in chunks)
    assert any(chunk.rstrip().endswith(",") for chunk in chunks)


def test_split_line_short_keeps_a_single_over_cap_word_intact():
    long_word = "x" * 90
    text = f"short words {long_word} trailing words"

    chunks = _split_line_short(text, 20)

    assert "".join(chunks) == text
    assert long_word in chunks
    assert all(len(chunk) <= 20 or long_word in chunk for chunk in chunks)
