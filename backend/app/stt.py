"""STT side: forward browser audio to Soniox, route translation/<end> tokens
into the TTS queue, and pass STT JSON straight to the browser for rendering.
"""

import asyncio
import json
import re
from contextlib import suppress
from typing import Any
from collections.abc import Awaitable, Callable

import httpx
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from .config import STT_KEEPALIVE_INTERVAL
from .logging_config import get_logger

log = get_logger("stt")

# Queue payload tags.
TTS_TEXT = "text"
TTS_END = "end"
TTS_NONE = None
# TTS chunks are deliberately short so synthesis/playback can start before a
# long utterance reaches Soniox's real endpoint. This is audio buffering only;
# the frontend coalesces non-endpoint `line_ready` messages for display.
LINE_MAX_CHARS = 50
# External (non-Soniox) translation providers only get a single sentence
# boundary hint (".", "!", "?", "…", ";") to fire an incremental translate
# call. Continuous speech without punctuation for a while would otherwise
# never get translated until the utterance's <end> token, which is exactly
# the "TTS+translation batches into one big paragraph" symptom. This cap
# forces an incremental translate call at a safe word boundary once the
# untranslated tail grows past it, even without sentence punctuation.
EXT_TRANSLATE_FALLBACK_CHARS = 80



async def pipe_browser_to_stt(
    browser_ws: WebSocket,
    stt_ws,
    on_text: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    """Single ingress pipe: forward binary audio to STT, dispatch text control
    frames to `on_text` (utterance snapshots, barge-in, etc.). One coroutine
    per WebSocket — avoids two consumers racing on receive()."""
    while True:
        msg = await browser_ws.receive()
        if "bytes" in msg and msg["bytes"] is not None:
            await stt_ws.send(msg["bytes"])
        elif "text" in msg and msg["text"] is not None and on_text is not None:
            try:
                data = json.loads(msg["text"])
            except json.JSONDecodeError:
                continue
            await on_text(data)


async def stream_url_to_stt(
    audio_url: str, duration: float, stt_ws, browser_ws: WebSocket
) -> None:
    """Stream a hosted audio file to STT at real-time pace (file test mode).

    Always sends the b"" end-of-stream signal to STT, even if the download
    fails partway — otherwise STT never finalizes and the session hangs.
    """
    loop = asyncio.get_running_loop()
    sent_end_signal = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
            async with client.stream("GET", audio_url, follow_redirects=True) as resp:
                resp.raise_for_status()
                content_length = int(resp.headers.get("content-length", 0))
                byte_rate = content_length / duration if content_length else 16000
                bytes_per_tick = max(1, int(byte_rate * 0.1))

                buffer = bytearray()
                next_tick = loop.time()
                async for chunk in resp.aiter_bytes():
                    buffer.extend(chunk)
                    while len(buffer) >= bytes_per_tick:
                        await stt_ws.send(bytes(buffer[:bytes_per_tick]))
                        del buffer[:bytes_per_tick]
                        next_tick += 0.1
                        delay = next_tick - loop.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                if buffer:
                    await stt_ws.send(bytes(buffer))
    except httpx.HTTPError as e:
        log.warning("audio_fetch_failed", url=audio_url, error=str(e))
        try:
            await browser_ws.send_json(
                {"error_code": "fetch_failed", "error_message": str(e)}
            )
        except Exception:
            pass
    finally:
        # ALWAYS send the end-of-stream signal so STT finalizes and sends
        # `finished: true`. Without this, the session hangs forever.
        if not sent_end_signal:
            try:
                await stt_ws.send(b"")
                sent_end_signal = True
            except Exception as e:
                log.warning("stt_end_signal_failed", error=str(e))


async def handle_stt(
    stt_ws,
    browser_ws: WebSocket,
    tts_queue: asyncio.Queue[Any] | None,
    tts_state: dict,
    mode: str,
    lang_a: str | None,
    lang_b: str | None,
    target_lang: str | None,
    on_endpoint: Callable[[], Awaitable[None]] | None = None,
    on_final_segment: Callable[[dict], Awaitable[None]] | None = None,
    finalize_session_on_exit: bool = True,
    finished_event: asyncio.Event | None = None,
    extra_hold_ms: int = 0,
    translate_text: Callable[[str, str | None, str], Awaitable[str]] | None = None,
) -> None:
    """Read STT responses: forward to browser, route tokens to TTS queue,
    call `on_endpoint` whenever an <end> token closes an utterance,
    and call `on_final_segment` with accumulated text when utterance ends."""
    text_pushed = False
    current_original = ""
    current_translation = ""
    full_translation = ""
    current_speaker = None
    current_lang = None
    line_original_offset = 0
    line_counter = int(tts_state.get("line_counter", 0))
    utterance_start_ms: int | None = None
    stream_finished = False
    pending_tts_chunks: list[tuple[str, str | None, dict[str, Any]]] = []
    # External translation streaming state.
    # ext_translate_offset: how many chars of current_original have already been
    # handed off to a translate task (so we don't re-translate the same text).
    ext_translate_offset = 0
    # Each entry: (original_slice, target_lang, start_char, end_char, asyncio.Task)
    ext_translate_tasks: list[tuple[str, str, int, int, asyncio.Task[str]]] = []
    hold_queue: asyncio.Queue[
        tuple[float, list[tuple[str, str | None, dict[str, Any]]], str | None, dict | None]
    ] | None = None
    hold_worker: asyncio.Task[None] | None = None

    def make_line_ready_payload(
        *,
        speaker: Any,
        original_text: str,
        translated_text: str,
        lang: str | None,
        is_endpoint: bool,
    ) -> dict[str, Any]:
        """Allocate the stable ID shared by one rendered line and its audio."""
        nonlocal line_counter
        line_counter += 1
        tts_state["line_counter"] = line_counter
        return _line_ready_payload(
            line_id=line_counter,
            speaker=speaker,
            original_text=original_text,
            translated_text=translated_text,
            lang=lang,
            is_endpoint=is_endpoint,
        )

    async def commit_utterance(
        tts_chunks: list[tuple[str, str | None, dict[str, Any]]],
        direction: str | None,
        final_segment: dict | None,
    ) -> None:
        for chunk, chunk_direction, line_payload in tts_chunks:
            await browser_ws.send_json(line_payload)
            if tts_queue is not None and tts_state.get("enabled", True) and chunk:
                await tts_queue.put(
                    (TTS_TEXT, chunk, chunk_direction, line_payload["line_id"])
                )
        if tts_queue is not None and tts_state.get("enabled", True):
            await tts_queue.put((TTS_END, direction))
        if on_endpoint is not None:
            await on_endpoint()
        if on_final_segment is not None and final_segment is not None:
            await on_final_segment(final_segment)

    if extra_hold_ms > 0:
        hold_queue = asyncio.Queue()

        async def release_held_utterances() -> None:
            assert hold_queue is not None
            loop = asyncio.get_running_loop()
            while True:
                release_at, tts_chunks, direction, final_segment = await hold_queue.get()
                try:
                    delay = release_at - loop.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await commit_utterance(tts_chunks, direction, final_segment)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("stt_hold_commit_failed", error=str(exc))
                finally:
                    hold_queue.task_done()

        hold_worker = asyncio.create_task(release_held_utterances())

    try:
        while True:
            message = await stt_ws.recv()
            data = json.loads(message)
            await browser_ws.send_json(data)

            if data.get("error_code") is not None:
                log.error("stt_error", error_code=data["error_code"], error_message=data["error_message"])
                break

            got_end = False
            message_has_end = any(
                token.get("text") == "<end>" for token in data.get("tokens", [])
            )
            for token in data.get("tokens", []):
                text = token.get("text")
                if not text:
                    continue

                if token.get("speaker") is not None and current_speaker is None:
                    current_speaker = token["speaker"]
                if token.get("language") and not current_lang:
                    current_lang = token["language"]
                if utterance_start_ms is None:
                    utterance_start_ms = data.get("start_time_ms") or 0

                if text == "<end>":
                    got_end = True
                    direction = (
                        target_lang
                        if mode == "one_way"
                        else _direction(token, mode, lang_a, lang_b, source_lang=current_lang)
                    )
                    tts_chunks = pending_tts_chunks
                    pending_tts_chunks = []
                    if translate_text is not None:
                        ext_target = _external_translation_target(
                            mode, current_lang, lang_a, lang_b, target_lang
                        )
                        if ext_target:
                            direction = ext_target
                            # Await any sentence-translate tasks that were fired mid-utterance
                            # but haven't finished yet.  Add their output to this commit batch.
                            still_running = list(ext_translate_tasks)
                            ext_translate_tasks.clear()
                            for _sl, _tgt, _st, end_off, task in still_running:
                                try:
                                    xlated = await task
                                except Exception as exc:
                                    log.error("ext_translate_partial_failed_at_end", error=str(exc))
                                    continue
                                if not xlated:
                                    continue
                                full_translation += xlated
                                parts = _split_line_short(xlated, LINE_MAX_CHARS) or [""]
                                for idx, part in enumerate(parts):
                                    orig_chunk = (
                                        current_original[line_original_offset:end_off]
                                        if idx == 0
                                        else ""
                                    )
                                    if idx == 0:
                                        line_original_offset = end_off
                                    lp = make_line_ready_payload(
                                        speaker=current_speaker,
                                        original_text=orig_chunk,
                                        translated_text=part,
                                        lang=current_lang,
                                        is_endpoint=False,
                                    )
                                    tts_chunks.append((part, ext_target, lp))
                            # Translate only the tail not yet covered by any task.
                            remaining_tail = current_original[ext_translate_offset:]
                            if remaining_tail:
                                try:
                                    current_translation = await translate_text(
                                        remaining_tail, current_lang, ext_target
                                    )
                                    full_translation += current_translation
                                except Exception as exc:
                                    log.error("external_translation_failed", error=str(exc))
                                    await browser_ws.send_json({
                                        "translation_error": {"message": str(exc)}
                                    })
                            else:
                                # All text was already translated incrementally.
                                current_translation = ""
                    remaining_original = current_original[line_original_offset:]
                    if current_translation or remaining_original:
                        translated_parts = _split_line_short(
                            current_translation, LINE_MAX_CHARS
                        ) or [""]
                        for index, translated_part in enumerate(translated_parts):
                            tts_chunks.append((
                                translated_part,
                                direction,
                                make_line_ready_payload(
                                    speaker=current_speaker,
                                    original_text=(
                                        remaining_original if index == 0 else ""
                                    ),
                                    translated_text=translated_part,
                                    lang=current_lang,
                                    is_endpoint=index == len(translated_parts) - 1,
                                ),
                            ))
                    final_segment = None
                    if current_original or full_translation:
                        final_segment = {
                            "original_text": current_original,
                            "translated_text": full_translation,
                            "speaker_label": str(current_speaker) if current_speaker is not None else None,
                            "source_lang": current_lang,
                            "started_at_ms": utterance_start_ms,
                            "ended_at_ms": data.get("end_time_ms") or (utterance_start_ms or 0) + 2000,
                        }
                    if hold_queue is not None:
                        release_at = (
                            asyncio.get_running_loop().time() + extra_hold_ms / 1000
                        )
                        hold_queue.put_nowait(
                            (release_at, tts_chunks, direction, final_segment)
                        )
                    else:
                        await commit_utterance(tts_chunks, direction, final_segment)
                    current_original = ""
                    current_translation = ""
                    full_translation = ""
                    current_speaker = None
                    current_lang = None
                    line_original_offset = 0
                    utterance_start_ms = None
                    ext_translate_offset = 0
                    ext_translate_tasks.clear()
                elif token.get("translation_status") == "translation":
                    if token.get("is_final"):
                        current_translation += text
                        full_translation += text
                        if tts_queue is not None:
                            target = _resolve_tts_target(
                                token, mode, lang_a, lang_b, target_lang
                            )
                            while (
                                not message_has_end
                                and len(current_translation) > LINE_MAX_CHARS
                            ):
                                split_at = _tts_buffer_split_index(current_translation)
                                if split_at is None:
                                    break
                                chunk = current_translation[:split_at]
                                current_translation = current_translation[split_at:]
                                original_chunk = current_original[line_original_offset:]
                                line_original_offset = len(current_original)
                                line_payload = make_line_ready_payload(
                                    speaker=current_speaker,
                                    original_text=original_chunk,
                                    translated_text=chunk,
                                    lang=current_lang,
                                    is_endpoint=False,
                                )
                                if hold_queue is not None:
                                    pending_tts_chunks.append((chunk, target, line_payload))
                                else:
                                    await browser_ws.send_json(line_payload)
                                    if tts_state.get("enabled", True):
                                        await tts_queue.put(
                                            (TTS_TEXT, chunk, target, line_payload["line_id"])
                                        )
                    text_pushed = True
                else:
                    if token.get("is_final"):
                        current_original += text

            if tts_queue is not None and not got_end:
                for token in data.get("tokens", []):
                    text = token.get("text")
                    if not text or text == "<end>" or token.get("translation_status") == "translation":
                        continue
                    text_pushed = True

            # ── Streaming external translation at sentence boundaries ────────────
            # For external translation providers we fire translate_text() as a
            # non-blocking asyncio.Task whenever we detect a sentence boundary in
            # the accumulated original text.  Completed tasks are flushed below so
            # TTS audio starts as soon as each sentence is translated rather than
            # waiting for the full <end> token.
            #
            # Continuous speech can go a long time without sentence punctuation
            # (run-on speech, no pauses). Without a fallback, nothing gets
            # translated/queued for TTS until the utterance's <end> token, which
            # produces one large paragraph dumped all at once instead of a
            # steady stream — so we also fire once the untranslated tail grows
            # past EXT_TRANSLATE_FALLBACK_CHARS, splitting at the last safe
            # word boundary so no word is cut in half.
            if translate_text is not None and not got_end and current_original:
                tail = current_original[ext_translate_offset:].rstrip()
                has_sentence_boundary = bool(tail) and tail[-1] in ".!?…;"
                fallback_split_at: int | None = None
                if not has_sentence_boundary and len(tail) >= EXT_TRANSLATE_FALLBACK_CHARS:
                    fallback_split_at = _tts_buffer_split_index(tail + " ")
                    # _tts_buffer_split_index expects text longer than
                    # LINE_MAX_CHARS; if it can't find punctuation/whitespace
                    # in range, fall back to splitting at the whole tail.
                    if fallback_split_at is None or fallback_split_at <= 0:
                        fallback_split_at = len(tail)
                if has_sentence_boundary or fallback_split_at is not None:
                    fire_text = tail if has_sentence_boundary else tail[:fallback_split_at]
                    ext_target = _external_translation_target(
                        mode, current_lang, lang_a, lang_b, target_lang
                    )
                    if ext_target and fire_text.strip():
                        end_off = ext_translate_offset + len(fire_text)
                        fired_task = asyncio.create_task(
                            translate_text(fire_text, current_lang, ext_target)
                        )
                        ext_translate_tasks.append(
                            (fire_text, ext_target, ext_translate_offset, end_off, fired_task)
                        )
                        ext_translate_offset = end_off


            # Flush any sentence-translate tasks that finished during the last recv().
            if ext_translate_tasks:
                still_pending_ext: list[tuple[str, str, int, int, asyncio.Task[str]]] = []
                for orig_slice, ext_tgt, _st, end_off, task in ext_translate_tasks:
                    if not task.done():
                        still_pending_ext.append((orig_slice, ext_tgt, _st, end_off, task))
                        continue
                    try:
                        xlated = task.result()
                    except Exception as exc:
                        log.error("ext_translate_partial_failed", error=str(exc))
                        continue
                    if not xlated:
                        continue
                    full_translation += xlated
                    parts = _split_line_short(xlated, LINE_MAX_CHARS) or [""]
                    for idx, part in enumerate(parts):
                        orig_chunk = (
                            current_original[line_original_offset:end_off]
                            if idx == 0
                            else ""
                        )
                        if idx == 0:
                            line_original_offset = end_off
                        lp = make_line_ready_payload(
                            speaker=current_speaker,
                            original_text=orig_chunk,
                            translated_text=part,
                            lang=current_lang,
                            is_endpoint=False,
                        )
                        if hold_queue is not None:
                            pending_tts_chunks.append((part, ext_tgt, lp))
                        else:
                            await browser_ws.send_json(lp)
                            if tts_queue is not None and tts_state.get("enabled", True) and part:
                                await tts_queue.put(
                                    (TTS_TEXT, part, ext_tgt, lp["line_id"])
                                )
                ext_translate_tasks = still_pending_ext

            if data.get("finished"):
                stream_finished = True
                if finished_event is not None:
                    finished_event.set()
                break
    except (WebSocketDisconnect, RuntimeError, websockets.ConnectionClosedOK):
        pass
    except websockets.ConnectionClosedError as e:
        log.warning("stt_ws_closed", error=str(e))
    finally:
        # Cancel any external-translation tasks that are still in flight
        # (e.g. session aborted mid-utterance, barge-in, network drop).
        for _, _, _, _, task in ext_translate_tasks:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        ext_translate_tasks.clear()
        try:
            if hold_queue is not None and hold_worker is not None:
                current_task = asyncio.current_task()
                is_being_cancelled = (
                    current_task is not None and current_task.cancelling() > 0
                )
                try:
                    if stream_finished and not is_being_cancelled:
                        await hold_queue.join()
                finally:
                    hold_worker.cancel()
                    with suppress(asyncio.CancelledError):
                        await hold_worker
        finally:
            # A transient Soniox disconnect must not terminate the shared TTS
            # queue. The reconnect loop owns final session cleanup and passes
            # finalize_session_on_exit=False for each individual STT connection.
            if finalize_session_on_exit or stream_finished:
                if tts_queue is not None:
                    await tts_queue.put((TTS_END, None))
                    await tts_queue.put(TTS_NONE)
                tts_state["stt_done"] = True
                if tts_queue is None or not text_pushed:
                    try:
                        await browser_ws.send_json({"session_done": True})
                    except Exception:
                        pass


def _resolve_tts_target(
    token: dict,
    mode: str,
    lang_a: str | None,
    lang_b: str | None,
    target_lang: str | None,
) -> str | None:
    """Target language for this translation token's TTS stream.

    Per the Soniox STS guide ("pick which to play based on the translation
    token's language field"), we prefer the explicit ``language`` field that
    Soniox sets on translation tokens when language identification is on.
    That field holds the language the translation is in (i.e. the TTS
    direction to play). We fall back to ``source_language`` and then to
    the per-utterance ``current_lang`` tracked upstream for older token
    shapes that lack both fields.
    """
    if mode == "one_way":
        return target_lang
    # Preferred: the language field on the translation token, when present
    # and inside the two-way conversation pair.
    tgt = token.get("language")
    if tgt and tgt in (lang_a, lang_b):
        return tgt
    # Fallback A: a legacy source_language field on the token.
    src = token.get("source_language")
    if src == lang_a:
        return lang_b
    if src == lang_b:
        return lang_a
    return target_lang


def _external_translation_target(
    mode: str,
    source_lang: str | None,
    lang_a: str | None,
    lang_b: str | None,
    target_lang: str | None,
) -> str | None:
    if mode == "one_way":
        return target_lang
    if source_lang == lang_a:
        return lang_b
    if source_lang == lang_b:
        return lang_a
    return target_lang or lang_b


def _line_ready_payload(
    *,
    line_id: int,
    speaker: Any,
    original_text: str,
    translated_text: str,
    lang: str | None,
    is_endpoint: bool,
) -> dict[str, Any]:
    return {
        "type": "line_ready",
        "line_id": line_id,
        "speaker": speaker,
        "original_text": original_text,
        "translated_text": translated_text,
        "lang": lang,
        "is_endpoint": is_endpoint,
    }


def _tts_buffer_split_index(text: str) -> int | None:
    """Return a safe split at or just before the TTS buffer threshold."""
    if len(text) <= LINE_MAX_CHARS:
        return None

    limit = min(LINE_MAX_CHARS, len(text) - 1)
    punctuation_split = None
    for index in range(limit):
        if text[index] in ".!?…;" and text[index + 1].isspace():
            punctuation_split = index + 2
    if punctuation_split is not None:
        return punctuation_split

    whitespace_index = max(
        (index for index in range(limit) if text[index].isspace()),
        default=-1,
    )
    return whitespace_index + 1 if whitespace_index >= 0 else None


def _split_line_short(text: str, cap: int) -> list[str]:
    """Split completed text into short, lossless, whole-word TTS lines.

    Boundaries are preferred in this order: sentence punctuation, commas,
    then whitespace. Separating whitespace stays attached to a chunk so
    joining the returned list always reconstructs ``text`` exactly.
    """
    if not text:
        return []
    if cap <= 0:
        raise ValueError("cap must be positive")

    sentence_parts = _split_including_separators(
        text, re.compile(r"(?<=[.!?…;])\s+|\n+")
    )
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    def append_piece(piece: str) -> None:
        nonlocal current
        if not piece:
            return
        if len(piece) > cap:
            flush_current()
            chunks.extend(_pack_long_sentence(piece, cap))
        elif current and len(current) + len(piece) > cap:
            flush_current()
            current = piece
        else:
            current += piece

    for sentence_part in sentence_parts:
        append_piece(sentence_part)
    flush_current()
    return chunks


def _pack_long_sentence(text: str, cap: int) -> list[str]:
    """Pack a long sentence by comma, falling back to whole words."""
    comma_parts: list[str] = []
    start = 0
    for match in re.finditer(r",", text):
        comma_parts.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        comma_parts.append(text[start:])

    chunks: list[str] = []
    current = ""
    for part in comma_parts:
        if len(part) > cap:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_pack_whole_words(part, cap))
        elif current and len(current) + len(part) > cap:
            chunks.append(current)
            current = part
        else:
            current += part
    if current:
        chunks.append(current)
    return chunks


def _pack_whole_words(text: str, cap: int) -> list[str]:
    """Pack text by whitespace without ever splitting a non-space token."""
    tokens = re.findall(r"\S+|\s+", text)
    chunks: list[str] = []
    current = ""
    for token in tokens:
        if current and len(current) + len(token) > cap:
            chunks.append(current)
            current = ""
        if len(token) > cap:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(token)
        else:
            current += token
    if current:
        chunks.append(current)
    return chunks


def _split_including_separators(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Split after matched separators, retaining every original character."""
    parts: list[str] = []
    start = 0
    for match in pattern.finditer(text):
        parts.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        parts.append(text[start:])
    return parts


def _direction(
    token: dict,
    mode: str,
    lang_a: str | None,
    lang_b: str | None,
    source_lang: str | None = None,
) -> str | None:
    """A key identifying which TTS direction an <end> belongs to.

    Returns the target language for two_way (the speaker's *other* language),
    or None for one_way (single direction). When the <end> token carries a
    ``language`` field we use it directly; otherwise we fall back to
    ``source_language`` and finally to the per-utterance ``current_lang``
    tracked by the caller (the language the speaker used to start this
    utterance)."""
    if mode != "two_way":
        return None
    tgt = token.get("language")
    if tgt and tgt in (lang_a, lang_b):
        return tgt
    src = token.get("source_language") or source_lang
    if src == lang_a:
        return lang_b
    if src == lang_b:
        return lang_a
    return None


async def stt_keepalive(stt_ws) -> None:
    """Send periodic keepalive pings to the STT WebSocket to prevent
    server-side timeout (code 1011: keepalive ping timeout)."""
    try:
        while True:
            await asyncio.sleep(STT_KEEPALIVE_INTERVAL)
            await stt_ws.send(json.dumps({"type": "keepalive"}))
    except websockets.ConnectionClosedOK:
        pass
    except websockets.ConnectionClosedError as e:
        log.warning("stt_keepalive_stopped", error=str(e))
