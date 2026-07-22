"""TTS side.

A single WebSocket connection to Soniox hosts up to 5 concurrent streams
multiplexed by `stream_id` (Soniox docs: "A single connection can host up to
5 concurrent streams"). We use one stream per *direction* (target language)
and one stream per rendered line within a direction.

Mode shape
----------
- one_way: 1 direction == `target_lang`.
- two_way: 2 directions (target = language B for speaker A, and target =
  language A for speaker B). Utterances in each direction get their own
  stream on the same WebSocket.

State
-----
`tts_state` is keyed by direction (target language code):

    tts_state = {
        "directions": {
            "es": {"current_stream_id": None, "stream_used": False, "idle_event": <Event>},
            "en": {"current_stream_id": None, "stream_used": False, "idle_event": <Event>},
        },
        "stream_id_to_direction": {
            "utterance-1": {"direction": "es", "line_id": 1},
            "prewarm-es": {"direction": "es", "line_id": None},
            ...,
        },
        "line_counter": 0,
        "prewarm_counter": 0,
        "stt_done": False,
        "enabled": True,
        "barge_epoch": 0,
    }

Queue payloads (from `stt.handle_stt`):
- `(TTS_TEXT, text, target, line_id)` — text for the rendered `line_id`
- `(TTS_END, direction)`      — `<end>` token, finalize the current utterance in `direction`
- `(TTS_BARGE, None)`         — barge-in: drain subsequent items of the same epoch and cancel
- `TTS_NONE`                  — session over sentinel, stop the sender
"""

import asyncio
import base64
import json
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from .config import (
    TTS_AUDIO_FORMAT,
    TTS_KEEPALIVE_INTERVAL,
    TTS_MODEL,
    TTS_SAMPLE_RATE,
)
from . import config as runtime_config
from .logging_config import get_logger
from .stt import TTS_END, TTS_NONE, TTS_TEXT

log = get_logger("tts")

TTS_BARGE = "barge"

PREWARM_STREAM_ID = "prewarm"
PREWARM_REUSE_WINDOW_SECONDS = 2.0


def get_tts_config(stream_id: str, voice: str, lang: str) -> dict[str, Any]:
    return {
        "api_key": runtime_config.SONIOX_API_KEY,
        "stream_id": stream_id,
        "model": TTS_MODEL,
        "voice": voice,
        "language": lang,
        "audio_format": TTS_AUDIO_FORMAT,
        "sample_rate": TTS_SAMPLE_RATE,
    }


def new_tts_state(directions: list[str]) -> dict[str, Any]:
    """Build a fresh, empty multi-direction TTS state."""
    return {
        "directions": {
            d: {
                "current_stream_id": None,
                "stream_used": False,
                "idle_event": asyncio.Event(),
            }
            for d in directions
        },
        "stream_id_to_direction": {},
        "line_counter": 0,
        "stt_done": False,
        "barge_epoch": 0,
        "barge_lock": asyncio.Lock(),
    }


async def prewarm_stream(
    tts_ws,
    tts_state: dict,
    direction: str,
    voice: str,
) -> None:
    """Pre-open a TTS stream for `direction` so the first utterance doesn't
    pay the round-trip for stream setup. Idempotent — skips if already open."""
    d = tts_state["directions"].get(direction)
    if d is None or d["current_stream_id"] is not None:
        return
    tts_state["prewarm_counter"] = int(tts_state.get("prewarm_counter", 0)) + 1
    stream_id = f"{PREWARM_STREAM_ID}-{tts_state['prewarm_counter']}-{direction}"
    try:
        await tts_ws.send(json.dumps(get_tts_config(stream_id=stream_id, voice=voice, lang=direction)))
        d["current_stream_id"] = stream_id
        # A prewarmed stream does not belong to a line until its first text.
        tts_state["stream_id_to_direction"][stream_id] = {
            "direction": direction,
            "line_id": None,
            "is_prewarm": True,
            "opened_at": asyncio.get_running_loop().time(),
        }
        d["idle_event"].clear()
    except websockets.WebSocketException:
        pass


async def tts_sender(
    tts_queue: asyncio.Queue[Any],
    tts_state: dict,
    tts_ws,
    direction_voices: dict[str, str],
    browser_ws: WebSocket | None = None,
) -> None:
    """Consume `tts_queue`: keep one TTS stream per rendered line and
    direction, forward text chunks, finalize on `TTS_END`, and
    honor barge-in by dropping stale items and cancelling open streams."""
    stream_counter = 0
    my_epoch = 0
    direction_char_counts = {direction: 0 for direction in direction_voices}
    try:
        while True:
            data = await tts_queue.get()
            if data is TTS_NONE:
                break

            kind = data[0]

            if kind == TTS_BARGE:
                # Drain any items still queued in this barge epoch, then
                # cancel currently-open streams so Soniox stops synthesizing.
                await _drain_queue(tts_queue)
                my_epoch = tts_state["barge_epoch"]
                await _cancel_open_streams(tts_ws, tts_state)
                continue

            # Drop items queued before the most recent barge-in.
            if my_epoch != tts_state["barge_epoch"]:
                continue

            if kind == TTS_TEXT:
                _, payload, target, line_id = data
                direction = target
                d = tts_state["directions"].get(direction)
                if d is None:
                    # Unknown direction (e.g. two_way with source outside
                    # lang_a/lang_b): drop silently.
                    continue

                # One Soniox stream belongs to exactly one rendered line. If
                # STT split a long utterance into multiple lines, finish the
                # previous line before assigning a stream to the next one.
                current_sid = d["current_stream_id"]
                current_meta = tts_state["stream_id_to_direction"].get(
                    current_sid, {}
                )
                if (
                    current_sid is not None
                    and not d["stream_used"]
                    and current_meta.get("is_prewarm")
                    and asyncio.get_running_loop().time()
                    - float(current_meta.get("opened_at", 0.0))
                    > PREWARM_REUSE_WINDOW_SECONDS
                ):
                    # A config-only stream can expire while STT is still
                    # producing its first stable translation token. Rotate a
                    # stale prewarm before binding the line so a queued 408
                    # cannot consume the text and leave the UI on retry.
                    await tts_ws.send(
                        json.dumps({"stream_id": current_sid, "cancel": True})
                    )
                    tts_state["stream_id_to_direction"].pop(current_sid, None)
                    d["current_stream_id"] = None
                    d["stream_used"] = False
                    d["idle_event"].set()
                    current_sid = None
                if current_sid is not None and d["stream_used"]:
                    current_meta = tts_state["stream_id_to_direction"].get(
                        current_sid, {}
                    )
                    if current_meta.get("line_id") != line_id:
                        await tts_ws.send(
                            json.dumps(
                                {
                                    "stream_id": current_sid,
                                    "text": "",
                                    "text_end": True,
                                }
                            )
                        )
                        d["current_stream_id"] = None
                        d["stream_used"] = False

                # Open a fresh line stream if idle.
                if d["current_stream_id"] is None:
                    await d["idle_event"].wait()
                    # Skip if a barge arrived while waiting.
                    if my_epoch != tts_state["barge_epoch"]:
                        continue
                    stream_counter += 1
                    stream_id = f"utterance-{stream_counter}-{direction}"
                    await tts_ws.send(
                        json.dumps(
                            get_tts_config(
                                stream_id=stream_id,
                                voice=direction_voices[direction],
                                lang=direction,
                            )
                        )
                    )
                    d["current_stream_id"] = stream_id
                    tts_state["stream_id_to_direction"][stream_id] = {
                        "direction": direction,
                        "line_id": line_id,
                    }
                    d["idle_event"].clear()
                else:
                    # Bind a prewarmed stream to the first line that uses it.
                    current_meta = tts_state["stream_id_to_direction"].get(
                        d["current_stream_id"]
                    )
                    if current_meta is not None and current_meta.get("line_id") is None:
                        current_meta["line_id"] = line_id

                await tts_ws.send(
                    json.dumps(
                        {
                            "stream_id": d["current_stream_id"],
                            "text": payload,
                            "text_end": False,
                        }
                    )
                )
                d["stream_used"] = True
                direction_char_counts[direction] = direction_char_counts.get(direction, 0) + len(payload)

            elif kind == TTS_END:
                _, direction = data
                # direction may be None for the trailing injection from
                # `handle_stt`'s finally; apply to any direction with an
                # open stream in that case.
                targets = [direction] if direction else list(tts_state["directions"])
                for tgt in targets:
                    d = tts_state["directions"].get(tgt)
                    if d is None:
                        continue
                    if d["current_stream_id"] is not None and d["stream_used"]:
                        sid = d["current_stream_id"]
                        await tts_ws.send(
                            json.dumps(
                                {
                                    "stream_id": sid,
                                    "text": "",
                                    "text_end": True,
                                }
                            )
                        )
                        d["current_stream_id"] = None
                        d["stream_used"] = False
                        # Keep stream_id_to_direction entry — Soniox WILL send
                        # a `terminated` event for text_end streams, which
                        # pipe_tts_to_browser uses to fire session_done.
                    elif d["current_stream_id"] is not None and not d["stream_used"]:
                        # Pre-warmed stream that never received text: cancel it
                        # to release the slot.
                        sid = d["current_stream_id"]
                        await tts_ws.send(
                            json.dumps(
                                {"stream_id": sid, "cancel": True}
                            )
                        )
                        d["current_stream_id"] = None
                        # Soniox may NOT send a `terminated` event for a
                        # cancelled stream. Remove from the routing map so
                        # the session_done check (which requires the map to
                        # be empty) can fire.
                        tts_state["stream_id_to_direction"].pop(sid, None)
                    char_count = direction_char_counts.get(tgt, 0)
                    if char_count and browser_ws is not None:
                        try:
                            await browser_ws.send_json({
                                "tts_usage": {
                                    "provider_id": "soniox",
                                    "voice_id": direction_voices[tgt],
                                    "characters": char_count,
                                    "estimated_cost_usd": 0.0,
                                    "cache_hit": False,
                                }
                            })
                        except Exception:
                            pass
                    direction_char_counts[tgt] = 0
    except websockets.ConnectionClosedOK:
        pass
    except websockets.ConnectionClosedError as e:
        log.warning("tts_sender_ws_closed", error=str(e))


async def pipe_tts_to_browser(
    tts_ws,
    browser_ws: WebSocket,
    tts_state: dict,
    direction_voices: dict[str, str] | None = None,
) -> None:
    """Read TTS responses (multiplexed by stream_id): forward base64 PCM to
    browser, route `terminated` events back to their direction state, and emit
    `session_done` once STT is finished and every direction is idle."""
    audio_end_streams: set[str] = set()

    async def rewarm_direction(direction: str | None) -> None:
        if (
            direction is None
            or direction_voices is None
            or tts_state.get("stt_done")
        ):
            return
        voice = direction_voices.get(direction)
        if voice is None:
            return
        await prewarm_stream(tts_ws, tts_state, direction, voice)

    async def send_audio_chunk(
        stream_id: str,
        audio: bytes,
        *,
        line_audio_end: bool,
    ) -> None:
        stream_meta = tts_state["stream_id_to_direction"].get(stream_id, {})
        # WebSocket guarantees message ordering on a single connection,
        # so this meta message always immediately precedes its binary payload.
        await browser_ws.send_json(
            {
                "type": "audio_chunk_meta",
                "line_id": stream_meta.get("line_id"),
                "byte_length": len(audio),
                "line_audio_end": line_audio_end,
            }
        )
        await browser_ws.send_bytes(audio)

    try:
        while True:
            # Use a timeout so we can check session_done even if Soniox
            # doesn't send any more messages (e.g. after cancelling streams).
            try:
                message = await asyncio.wait_for(tts_ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                # No TTS message in 5s — check if the session should be done.
                _maybe_send_session_done_sync(tts_state)
                if tts_state.get("_session_done_sent"):
                    try:
                        await browser_ws.send_json({"session_done": True})
                    except Exception:
                        pass
                    await tts_ws.close()
                    break
                continue

            data = json.loads(message)

            if data.get("error_code") is not None:
                error_stream_id = data.get("stream_id")
                log.error(
                    "tts_stream_error",
                    stream_id=error_stream_id,
                    error_code=data["error_code"],
                    error_message=data.get("error_message"),
                )

                # A config-only prewarm can expire before speech begins. That
                # is recoverable: the sender opens a fresh stream on the first
                # text token. Do not turn this into a user-facing "Read: retry".
                stream_meta = tts_state["stream_id_to_direction"].get(error_stream_id, {}) if error_stream_id else {}
                error_line_id = stream_meta.get("line_id")
                if error_line_id is not None:
                    try:
                        await browser_ws.send_json({
                            "type": "tts_error",
                            "line_id": error_line_id,
                            "error_code": data["error_code"],
                            "error_message": data.get("error_message", ""),
                        })
                    except Exception:
                        pass

                # Mark the line as "done" with 0 bytes audio so
                # StrictLineAudioQueue does not block on it forever.
                if error_stream_id:
                    if error_line_id is not None:
                        await send_audio_chunk(
                            error_stream_id,
                            b"",
                            line_audio_end=True,
                        )

                    # Clean up stream state — same logic as the `terminated` handler.
                    error_meta = tts_state["stream_id_to_direction"].pop(error_stream_id, None)
                    direction = error_meta.get("direction") if error_meta is not None else None
                    if direction is not None:
                        d = tts_state["directions"].get(direction)
                        if d is not None:
                            if d["current_stream_id"] == error_stream_id:
                                d["current_stream_id"] = None
                                d["stream_used"] = False
                            d["idle_event"].set()

                continue

            audio_b64 = data.get("audio")
            if audio_b64:
                audio = base64.b64decode(audio_b64)
                sid = data.get("stream_id")
                if sid is not None:
                    is_audio_end = bool(data.get("audio_end"))
                    if is_audio_end:
                        audio_end_streams.add(sid)
                    # Forward every PCM frame immediately. Soniox's audio_end
                    # flag identifies the last frame, so no look-ahead buffer
                    # is needed and first-audio latency stays minimal.
                    await send_audio_chunk(
                        sid, audio, line_audio_end=is_audio_end
                    )

            if data.get("terminated"):
                sid = data.get("stream_id")
                stream_meta_before_cleanup = tts_state[
                    "stream_id_to_direction"
                ].get(sid, {})
                # Be defensive with older/aborted streams that terminate
                # without an audio_end frame: a zero-byte end marker releases
                # the frontend's strict FIFO without synthesizing silence.
                if (
                    sid not in audio_end_streams
                    and stream_meta_before_cleanup.get("line_id") is not None
                ):
                    await send_audio_chunk(sid, b"", line_audio_end=True)
                audio_end_streams.discard(sid)
                stream_meta = tts_state["stream_id_to_direction"].pop(sid, None)
                direction = (
                    stream_meta.get("direction") if stream_meta is not None else None
                )
                if direction is not None:
                    d = tts_state["directions"].get(direction)
                    if d is not None:
                        if d["current_stream_id"] == sid:
                            d["current_stream_id"] = None
                            d["stream_used"] = False
                        d["idle_event"].set()

                await rewarm_direction(direction)

                _maybe_send_session_done_sync(tts_state)
                if tts_state.get("_session_done_sent"):
                    try:
                        await browser_ws.send_json({"session_done": True})
                    except Exception:
                        pass
                    await tts_ws.close()
                    break
            elif data.get("audio") is None and not data.get("keep_alive"):
                _maybe_send_session_done_sync(tts_state)
                if tts_state.get("_session_done_sent"):
                    try:
                        await browser_ws.send_json({"session_done": True})
                    except Exception:
                        pass
                    await tts_ws.close()
                    break
    except (WebSocketDisconnect, RuntimeError, websockets.ConnectionClosedOK):
        pass
    except websockets.ConnectionClosedError as e:
        log.warning("tts_pipe_closed", error=str(e))


async def tts_keepalive(tts_ws) -> None:
    try:
        while True:
            await asyncio.sleep(TTS_KEEPALIVE_INTERVAL)
            await tts_ws.send(json.dumps({"keep_alive": True}))
    except websockets.ConnectionClosedOK:
        pass
    except websockets.ConnectionClosedError as e:
        log.warning("tts_keepalive_stopped", error=str(e))


async def synthesize_soniox_text(text: str, voice: str, lang: str) -> bytes:
    """Synthesize one complete utterance through Soniox for provider fallback."""
    stream_id = "fallback"
    ws = await websockets.connect(
        runtime_config.TTS_URL,
        ping_interval=10,
        ping_timeout=10,
        close_timeout=5,
    )
    chunks: list[bytes] = []
    try:
        await ws.send(json.dumps(get_tts_config(stream_id=stream_id, voice=voice, lang=lang)))
        await ws.send(json.dumps({"stream_id": stream_id, "text": text, "text_end": False}))
        await ws.send(json.dumps({"stream_id": stream_id, "text": "", "text_end": True}))
        while True:
            message = await asyncio.wait_for(ws.recv(), timeout=30.0)
            data = json.loads(message)
            if data.get("error_code") is not None:
                raise RuntimeError(data.get("error_message") or str(data["error_code"]))
            if data.get("audio"):
                chunks.append(base64.b64decode(data["audio"]))
            if data.get("terminated"):
                break
    finally:
        await ws.close()
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("Soniox fallback returned no audio")
    return audio


# --------------------------------------------------------------------------- #
# Session-done helper
# --------------------------------------------------------------------------- #
def _maybe_send_session_done_sync(tts_state: dict) -> None:
    """Check if the session is done (STT finished + all directions idle) and
    set a flag. The caller reads the flag and sends the actual session_done
    message to the browser. Idempotent."""
    if tts_state.get("_session_done_sent"):
        return
    if not tts_state.get("stt_done"):
        return
    if not all(
        d["current_stream_id"] is None
        for d in tts_state["directions"].values()
    ):
        return
    # Used streams stay in this map until `terminated`, whose handler also
    # flushes the final look-ahead audio chunk. Cancelled streams are removed
    # by the sender/barge path, so a non-empty map means audio is still pending.
    if tts_state["stream_id_to_direction"]:
        return
    tts_state["_session_done_sent"] = True


# --------------------------------------------------------------------------- #
# Barge-in helpers
# --------------------------------------------------------------------------- #
async def trigger_barge(tts_queue: asyncio.Queue, tts_state: dict) -> None:
    """Called from the main WS handler when the browser signals a barge-in
    (user spoke over the playing TTS). Increments the barge epoch and enqueues
    a sentinel the sender will pick up to drain + cancel open streams."""
    async with tts_state["barge_lock"]:
        tts_state["barge_epoch"] += 1
        epoch = tts_state["barge_epoch"]
    # Put a barge sentinel in the queue; the sender drains and cancels.
    await tts_queue.put((TTS_BARGE, epoch))


async def _drain_queue(q: asyncio.Queue) -> None:
    """Best-effort drain of all currently-queued items without blocking."""
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
        except asyncio.QueueEmpty:
            break


async def _cancel_open_streams(tts_ws, tts_state: dict) -> None:
    """Send Soniox `cancel` for every currently-open TTS stream so it stops
    synthesizing immediately and frees the slot. Notifies the browser too."""
    for direction, d in tts_state["directions"].items():
        sid = d["current_stream_id"]
        if sid is None:
            continue
        try:
            await tts_ws.send(json.dumps({"stream_id": sid, "cancel": True}))
        except websockets.ConnectionClosedOK:
            pass
        # The terminated event from Soniox will come back via
        # `pipe_tts_to_browser` and reset current_stream_id. But set it
        # optimistically so we don't keep sending text to a cancelled stream.
        d["current_stream_id"] = None
        d["stream_used"] = False
    # Clear the reverse map; terminated events for these sids will be no-ops.
    tts_state["stream_id_to_direction"].clear()
