"""TTS side.

A single WebSocket connection to Soniox hosts up to 5 concurrent streams
multiplexed by `stream_id` (Soniox docs: "A single connection can host up to
5 concurrent streams"). We use one stream per *direction* (target language)
and one stream per utterance within a direction.

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
        "stream_id_to_direction": {"utterance-1": "es", "prewarm-es": "es", ...},
        "stt_done": False,
        "barge_epoch": 0,
    }

Queue payloads (from `stt.handle_stt`):
- `(TTS_TEXT, text, target)`  — translation token for `target` direction
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
    SONIOX_API_KEY,
    TTS_AUDIO_FORMAT,
    TTS_KEEPALIVE_INTERVAL,
    TTS_MODEL,
    TTS_SAMPLE_RATE,
)
from .logging_config import get_logger
from .stt import TTS_END, TTS_NONE, TTS_TEXT

log = get_logger("tts")

TTS_BARGE = "barge"

PREWARM_STREAM_ID = "prewarm"


def get_tts_config(stream_id: str, voice: str, lang: str) -> dict[str, Any]:
    return {
        "api_key": SONIOX_API_KEY,
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
    stream_id = f"{PREWARM_STREAM_ID}-{direction}"
    try:
        await tts_ws.send(json.dumps(get_tts_config(stream_id=stream_id, voice=voice, lang=direction)))
        d["current_stream_id"] = stream_id
        tts_state["stream_id_to_direction"][stream_id] = direction
        d["idle_event"].clear()
    except websockets.WebSocketException:
        pass


async def tts_sender(
    tts_queue: asyncio.Queue[Any],
    tts_state: dict,
    tts_ws,
    direction_voices: dict[str, str],
) -> None:
    """Consume `tts_queue`: open a fresh per-utterance TTS stream per
    direction on demand, forward text chunks, finalize on `TTS_END`, and
    honor barge-in by dropping stale items and cancelling open streams."""
    stream_counter = 0
    my_epoch = 0
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
                _, payload, target = data
                direction = target
                d = tts_state["directions"].get(direction)
                if d is None:
                    # Unknown direction (e.g. two_way with source outside
                    # lang_a/lang_b): drop silently.
                    continue

                # Open a fresh utterance stream if idle.
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
                    tts_state["stream_id_to_direction"][stream_id] = direction
                    d["idle_event"].clear()

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
                        await tts_ws.send(
                            json.dumps(
                                {
                                    "stream_id": d["current_stream_id"],
                                    "text": "",
                                    "text_end": True,
                                }
                            )
                        )
                        d["current_stream_id"] = None
                        d["stream_used"] = False
                        # idle_event will fire when Soniox confirms terminated
                        # in `pipe_tts_to_browser`.
                    elif d["current_stream_id"] is not None and not d["stream_used"]:
                        # Pre-warmed stream that never received text: cancel it
                        # to release the slot.
                        await tts_ws.send(
                            json.dumps(
                                {"stream_id": d["current_stream_id"], "cancel": True}
                            )
                        )
                        d["current_stream_id"] = None
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
    try:
        while True:
            message = await tts_ws.recv()
            data = json.loads(message)

            if data.get("error_code") is not None:
                log.error(
                    "tts_stream_error",
                    stream_id=data.get("stream_id"),
                    error_code=data["error_code"],
                    error_message=data["error_message"],
                )

            audio_b64 = data.get("audio")
            if audio_b64:
                await browser_ws.send_bytes(base64.b64decode(audio_b64))

            if data.get("terminated"):
                sid = data.get("stream_id")
                direction = tts_state["stream_id_to_direction"].pop(sid, None)
                if direction is not None:
                    d = tts_state["directions"].get(direction)
                    if d is not None:
                        if d["current_stream_id"] == sid:
                            d["current_stream_id"] = None
                            d["stream_used"] = False
                        d["idle_event"].set()

                # Once STT is done and no direction has an open stream, this
                # terminated event marked the very last TTS audio of the
                # session — tell the browser it's safe to stop.
                if (
                    tts_state["stt_done"]
                    and all(
                        d["current_stream_id"] is None
                        for d in tts_state["directions"].values()
                    )
                    and not tts_state["stream_id_to_direction"]
                ):
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