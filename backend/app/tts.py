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
            "es": {"streams": {}, "prewarmed": None, "seq": 0},
            "en": {"streams": {}, "prewarmed": None, "seq": 0},
        },
        "stream_id_to_direction": {
            "utt-1-es": {"direction": "es", "line_id": 1},
            "prewarm-es": {"direction": "es", "line_id": None},
            ...,
        },
        "line_counter": 0,
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
from .config_store import get_tts_api_key
from .logging_config import get_logger
from .stt import TTS_END, TTS_NONE, TTS_TEXT

log = get_logger("tts")

TTS_BARGE = "barge"

PREWARM_STREAM_ID = "prewarm"


def get_tts_config(stream_id: str, voice: str, lang: str) -> dict[str, Any]:
    # Prefer the per-provider saved TTS key; fall back to the global Soniox key.
    soniox_key = get_tts_api_key("soniox") or runtime_config.SONIOX_API_KEY
    return {
        "api_key": soniox_key,
        "stream_id": stream_id,
        "model": TTS_MODEL,
        "voice": voice,
        "language": lang,
        "audio_format": TTS_AUDIO_FORMAT,
        "sample_rate": TTS_SAMPLE_RATE,
    }


def new_tts_state(directions: list[str]) -> dict[str, Any]:
    """Build a fresh, empty multi-direction TTS state.

    Each direction keeps a *pool* of open stream slots instead of a single
    stream. This lets a new sentence open a fresh stream immediately (no
    waiting for the previous sentence to finish speaking), so TTS keeps up
    with streaming STT+translation in real time.
    """
    return {
        "directions": {
            d: {
                # Active streams for this direction: stream_id -> line_id.
                "streams": {},
                # Prewarmed-but-unused stream that can be claimed by the
                # first sentence that needs it.
                "prewarmed": None,
                # Count of stream ids handed out (for unique naming).
                "seq": 0,
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
    if d is None or d["prewarmed"] is not None:
        return
    stream_id = f"{PREWARM_STREAM_ID}-{direction}"
    try:
        await tts_ws.send(json.dumps(get_tts_config(stream_id=stream_id, voice=voice, lang=direction)))
        d["prewarmed"] = stream_id
        # A prewarmed stream does not belong to a line until its first text.
        tts_state["stream_id_to_direction"][stream_id] = {
            "direction": direction,
            "line_id": None,
        }
    except websockets.WebSocketException as exc:
        # Don't swallow silently — a failed prewarm means the first spoken
        # line will have no audio.  Log it so it's diagnosable.
        log.warning(
            "tts_prewarm_failed",
            direction=direction,
            error=str(exc),
            stream_id=stream_id,
        )


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
                async with tts_state["barge_lock"]:
                    my_epoch = tts_state["barge_epoch"]
                await _cancel_open_streams(tts_ws, tts_state)
                continue

            # Drop items queued before the most recent barge-in.
            async with tts_state["barge_lock"]:
                current_epoch = tts_state["barge_epoch"]
            if my_epoch != current_epoch:
                continue

            if kind == TTS_TEXT:
                _, payload, target, line_id = data
                direction = target
                d = tts_state["directions"].get(direction)
                if d is None:
                    # Unknown direction (e.g. two_way with source outside
                    # lang_a/lang_b): drop silently.
                    continue

                # Open a fresh stream for this sentence immediately — never
                # wait for a previous sentence to finish speaking. Each
                # sentence gets its own Soniox stream so TTS stays in lockstep
                # with streaming STT+translation (real-time dubbing), instead
                # of buffering a whole paragraph and reading it after STT
                # finishes.
                d["seq"] += 1
                stream_id = f"utt-{d['seq']}-{direction}"
                await tts_ws.send(
                    json.dumps(
                        get_tts_config(
                            stream_id=stream_id,
                            voice=direction_voices[direction],
                            lang=direction,
                        )
                    )
                )
                tts_state["stream_id_to_direction"][stream_id] = {
                    "direction": direction,
                    "line_id": line_id,
                }

                await tts_ws.send(
                    json.dumps(
                        {
                            "stream_id": stream_id,
                            "text": payload,
                            "text_end": True,  # one complete sentence per stream
                        }
                    )
                )
                direction_char_counts[direction] = direction_char_counts.get(direction, 0) + len(payload)

            elif kind == TTS_END:
                _, direction = data
                # With per-sentence streams, every text already carried
                # text_end=True, so there is nothing to flush here — streams
                # close themselves when Soniox finishes reading them. This
                # branch only clears usage counters (and any prewarmed stream
                # that was never claimed).
                targets = [direction] if direction else list(tts_state["directions"])
                for tgt in targets:
                    d = tts_state["directions"].get(tgt)
                    if d is None:
                        continue
                    # Cancel a prewarmed-but-unused stream to release the slot.
                    if d["prewarmed"] is not None:
                        sid = d["prewarmed"]
                        try:
                            await tts_ws.send(
                                json.dumps({"stream_id": sid, "cancel": True})
                            )
                        except websockets.WebSocketException:
                            pass
                        tts_state["stream_id_to_direction"].pop(sid, None)
                        d["prewarmed"] = None
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
) -> None:
    """Read TTS responses (multiplexed by stream_id): forward base64 PCM to
    browser, route `terminated` events back to their direction state, and emit
    `session_done` once STT is finished and every direction is idle."""
    pending_audio_by_stream: dict[str, bytes] = {}

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
                _maybe_send_session_done_sync(tts_state, pending_audio_empty=not pending_audio_by_stream)
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

                # Report error to the frontend so the user knows TTS is broken.
                stream_meta = tts_state["stream_id_to_direction"].get(error_stream_id, {}) if error_stream_id else {}
                error_line_id = stream_meta.get("line_id")
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
                    # Flush any pending audio for this stream (0 bytes).
                    pending_audio_by_stream.pop(error_stream_id, None)
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
                            d["streams"].pop(error_stream_id, None)
                            if d["prewarmed"] == error_stream_id:
                                d["prewarmed"] = None

                continue

            audio_b64 = data.get("audio")
            if audio_b64:
                audio = base64.b64decode(audio_b64)
                sid = data.get("stream_id")
                if sid is not None:
                    previous_audio = pending_audio_by_stream.pop(sid, None)
                    if previous_audio is not None:
                        await send_audio_chunk(
                            sid, previous_audio, line_audio_end=False
                        )
                    # Soniox marks stream completion on a later `terminated`
                    # event, so retain one chunk of look-ahead. That lets the
                    # browser know which binary payload is truly last.
                    pending_audio_by_stream[sid] = audio

            if data.get("terminated"):
                sid = data.get("stream_id")
                final_audio = pending_audio_by_stream.pop(sid, None)
                if final_audio is not None:
                    await send_audio_chunk(sid, final_audio, line_audio_end=True)
                stream_meta = tts_state["stream_id_to_direction"].pop(sid, None)
                direction = (
                    stream_meta.get("direction") if stream_meta is not None else None
                )
                if direction is not None:
                    d = tts_state["directions"].get(direction)
                    if d is not None:
                        d["streams"].pop(sid, None)
                        if d["prewarmed"] == sid:
                            d["prewarmed"] = None

                _maybe_send_session_done_sync(tts_state, pending_audio_empty=not pending_audio_by_stream)
                if tts_state.get("_session_done_sent"):
                    try:
                        await browser_ws.send_json({"session_done": True})
                    except Exception:
                        pass
                    await tts_ws.close()
                    break
            elif data.get("audio") is None and not data.get("keep_alive"):
                _maybe_send_session_done_sync(tts_state, pending_audio_empty=not pending_audio_by_stream)
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
def _maybe_send_session_done_sync(tts_state: dict, *, pending_audio_empty: bool = True) -> None:
    """Check if the session is done (STT finished + all directions idle) and
    set a flag. The caller reads the flag and sends the actual session_done
    message to the browser. Idempotent."""
    if tts_state.get("_session_done_sent"):
        return
    if not tts_state.get("stt_done"):
        return
    if not all(
        not d["streams"] and d["prewarmed"] is None
        for d in tts_state["directions"].values()
    ):
        return
    if not pending_audio_empty:
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
        # Cancel all active streams for this direction plus the prewarmed one.
        for sid in list(d["streams"]) + ([d["prewarmed"]] if d["prewarmed"] else []):
            try:
                await tts_ws.send(json.dumps({"stream_id": sid, "cancel": True}))
            except websockets.ConnectionClosedOK:
                pass
        d["streams"].clear()
        d["prewarmed"] = None
    # Clear the reverse map; terminated events for these sids will be no-ops.
    tts_state["stream_id_to_direction"].clear()
