"""STT-only browser audio, transcript, translation and line events."""

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

LINE_MAX_CHARS = 80


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
        if msg.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(
                code=msg.get("code", 1000),
                reason=msg.get("reason", ""),
            )
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
    session_state: dict[str, Any],
    mode: str,
    lang_a: str | None,
    lang_b: str | None,
    target_lang: str | None,
    on_endpoint: Callable[[], Awaitable[None]] | None = None,
    on_line_ready: Callable[[dict[str, Any], str | None], Awaitable[None]] | None = None,
    on_final_segment: Callable[[dict], Awaitable[None]] | None = None,
    finalize_session_on_exit: bool = True,
    finished_event: asyncio.Event | None = None,
    extra_hold_ms: int = 0,
    translate_text: Callable[[str, str | None, str], Awaitable[str]] | None = None,
    send_session_done_on_finish: bool = True,
) -> None:
    """Forward STT events and emit final translated lines independently."""
    current_original = ""
    current_translation = ""
    full_translation = ""
    current_speaker = None
    current_lang = None
    line_original_offset = 0
    line_counter = int(session_state.get("line_counter", 0))
    utterance_start_ms: int | None = None
    stream_finished = False
    pending_line_chunks: list[tuple[str, str | None, dict[str, Any]]] = []
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
        session_state["line_counter"] = line_counter
        return _line_ready_payload(
            line_id=line_counter,
            speaker=speaker,
            original_text=original_text,
            translated_text=translated_text,
            lang=lang,
            is_endpoint=is_endpoint,
        )

    async def commit_utterance(
        line_chunks: list[tuple[str, str | None, dict[str, Any]]],
        direction: str | None,
        final_segment: dict | None,
    ) -> None:
        for chunk, chunk_direction, line_payload in line_chunks:
            line_payload["target_lang"] = chunk_direction
            line_payload["direction"] = chunk_direction
            await browser_ws.send_json(line_payload)
            if on_line_ready is not None:
                await on_line_ready(line_payload, chunk_direction)
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
                release_at, line_chunks, direction, final_segment = await hold_queue.get()
                try:
                    delay = release_at - loop.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await commit_utterance(line_chunks, direction, final_segment)
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
            # Split-protocol frames always carry a discriminator while all
            # original Soniox fields remain available to legacy renderers.
            if "type" not in data:
                data["type"] = "transcript_partial"
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
                        else _direction(token, mode, lang_a, lang_b)
                    )
                    line_chunks = pending_line_chunks
                    pending_line_chunks = []
                    if translate_text is not None and current_original:
                        external_target = _external_translation_target(
                            mode, current_lang, lang_a, lang_b, target_lang
                        )
                        if external_target:
                            try:
                                current_translation = await translate_text(
                                    current_original, current_lang, external_target
                                )
                                full_translation = current_translation
                                direction = external_target
                            except Exception as exc:
                                log.error("external_translation_failed", error=str(exc))
                                await browser_ws.send_json({
                                    "translation_error": {"message": str(exc)}
                                })
                    remaining_original = current_original[line_original_offset:]
                    if current_translation or remaining_original:
                        translated_parts = _split_line_short(
                            current_translation, LINE_MAX_CHARS
                        ) or [""]
                        for index, translated_part in enumerate(translated_parts):
                            line_chunks.append((
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
                            (release_at, line_chunks, direction, final_segment)
                        )
                    else:
                        await commit_utterance(line_chunks, direction, final_segment)
                    current_original = ""
                    current_translation = ""
                    full_translation = ""
                    current_speaker = None
                    current_lang = None
                    line_original_offset = 0
                    utterance_start_ms = None
                elif token.get("translation_status") == "translation":
                    if token.get("is_final"):
                        current_translation += text
                        full_translation += text
                        target = _resolve_translation_target(
                            token, mode, lang_a, lang_b, target_lang
                        )
                        while (
                            not message_has_end
                            and len(current_translation) > LINE_MAX_CHARS
                        ):
                            split_at = _line_buffer_split_index(current_translation)
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
                                pending_line_chunks.append((chunk, target, line_payload))
                            else:
                                line_payload["target_lang"] = target
                                line_payload["direction"] = target
                                await browser_ws.send_json(line_payload)
                                if on_line_ready is not None:
                                    await on_line_ready(line_payload, target)
                else:
                    if token.get("is_final"):
                        current_original += text

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
            if finalize_session_on_exit or stream_finished:
                if send_session_done_on_finish:
                    try:
                        await browser_ws.send_json({"session_done": True})
                    except Exception:
                        pass


def _resolve_translation_target(
    token: dict,
    mode: str,
    lang_a: str | None,
    lang_b: str | None,
    target_lang: str | None,
) -> str | None:
    """Target language for this translation token's TTS stream."""
    if mode == "one_way":
        return target_lang
    # two_way: translate to the *other* language in the pair.
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


def _line_buffer_split_index(text: str) -> int | None:
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


def _direction(token: dict, mode: str, lang_a: str | None, lang_b: str | None) -> str | None:
    """A key identifying which TTS direction an <end> belongs to.

    Returns the target language for two_way (the speaker's *other* language),
    or None for one_way (single direction)."""
    if mode != "two_way":
        return None
    src = token.get("source_language")
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
