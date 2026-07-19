"""STT side: forward browser audio to Soniox, route translation/<end> tokens
into the TTS queue, and pass STT JSON straight to the browser for rendering.
"""

import asyncio
import json
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
) -> None:
    """Read STT responses: forward to browser, route tokens to TTS queue,
    call `on_endpoint` whenever an <end> token closes an utterance,
    and call `on_final_segment` with accumulated text when utterance ends."""
    text_pushed = False
    current_original = ""
    current_translation = ""
    current_speaker = None
    current_lang = None
    utterance_start_ms: int | None = None
    stream_finished = False
    try:
        while True:
            message = await stt_ws.recv()
            data = json.loads(message)
            await browser_ws.send_json(data)

            if data.get("error_code") is not None:
                log.error("stt_error", error_code=data["error_code"], error_message=data["error_message"])
                break

            got_end = False
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
                    direction = _direction(token, mode, lang_a, lang_b)
                    if tts_queue is not None:
                        await tts_queue.put((TTS_END, direction))
                    if on_endpoint is not None:
                        await on_endpoint()
                    if on_final_segment is not None and (current_original or current_translation):
                        await on_final_segment({
                            "original_text": current_original,
                            "translated_text": current_translation,
                            "speaker_label": str(current_speaker) if current_speaker is not None else None,
                            "source_lang": current_lang,
                            "started_at_ms": utterance_start_ms,
                            "ended_at_ms": data.get("end_time_ms") or (utterance_start_ms or 0) + 2000,
                        })
                        current_original = ""
                        current_translation = ""
                        current_speaker = None
                        current_lang = None
                        utterance_start_ms = None
                elif token.get("translation_status") == "translation":
                    if token.get("is_final"):
                        current_translation += text
                    target = _resolve_tts_target(
                        token, mode, lang_a, lang_b, target_lang
                    )
                    if tts_queue is not None:
                        await tts_queue.put((TTS_TEXT, text, target))
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
