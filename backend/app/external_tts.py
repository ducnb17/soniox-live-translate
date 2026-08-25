"""Queue consumer for non-Soniox TTS providers with cache and fallback.

Optimized for real-time dubbing:
- Start synthesizing each translated chunk as soon as it arrives (TTS_TEXT),
  not after TTS_END.
- Run synthesis concurrently.
- Emit audio in strict line order so the listener hears a coherent stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket

from .logging_config import get_logger
from .stt import TTS_END, TTS_NONE, TTS_TEXT
from .tts import TTS_BARGE, synthesize_soniox_text
from .tts_provider import TTSProviderBase, tts_cache

log = get_logger("external_tts")


async def _safe_json(browser_ws: WebSocket, payload: dict) -> None:
    try:
        await browser_ws.send_json(payload)
    except Exception:
        pass


async def external_tts_sender(
    tts_queue: asyncio.Queue,
    tts_state: dict,
    browser_ws: WebSocket,
    provider_id: str,
    provider: TTSProviderBase | None,
    direction_voices: dict[str, str],
    fallback_voice: str = "Maya",
    fallback_synthesize: Callable[[str, str, str], Awaitable[bytes]] = synthesize_soniox_text,
) -> None:
    """Consume TTS_TEXT items, synthesize in parallel, and emit audio in order."""
    my_epoch = tts_state["barge_epoch"]

    # Per-direction ordered state.
    class DirectionState:
        def __init__(self) -> None:
            self.next_seq = 0
            self.pending: dict[int, tuple[int, asyncio.Future[bytes]]] = {}
            self.cv = asyncio.Condition()

    states: dict[str, DirectionState] = {d: DirectionState() for d in direction_voices}

    async def synthesize_one(direction: str, text: str, seq: int, line_id: int, state: DirectionState, epoch: int) -> None:
        # Synthesize FIRST, then register the result — avoids race where
        # playback_loop awaits a future that is not yet scheduled.
        try:
            audio = await _run_synthesize(browser_ws, provider, provider_id, direction_voices[direction], direction, text, fallback_synthesize)
        except Exception:
            audio = b""
        fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        fut.set_result(audio)
        async with state.cv:
            # If a barge-in fired while we were synthesizing, our seq belongs
            # to the OLD epoch and `direction_seq`/`next_seq` were reset.
            # Drop the audio instead of polluting the new epoch's pending
            # map — otherwise playback_loop would wait forever for seq 0
            # while an entry with a stale seq occupies the map, and the
            # TTS_NONE drain (in_flight == 0 and not pending) deadlocks.
            if epoch != direction_epoch[direction]:
                in_flight[direction] -= 1
                state.cv.notify_all()
                return
            state.pending[seq] = (line_id, fut)
            in_flight[direction] -= 1
            state.cv.notify_all()

    async def playback_loop(direction: str, state: DirectionState) -> None:
        while True:
            async with state.cv:
                await state.cv.wait_for(lambda: state.next_seq in state.pending)
                seq = state.next_seq
                line_id, fut = state.pending.pop(seq)
            try:
                audio = await fut
            except asyncio.CancelledError:
                raise
            except Exception:
                audio = b""
            if audio:
                try:
                    await browser_ws.send_json({
                        "type": "audio_chunk_meta",
                        "line_id": line_id,
                        "byte_length": len(audio),
                        "line_audio_end": True,
                    })
                    await browser_ws.send_bytes(audio)
                except Exception:
                    pass  # log but NEVER return — keep the loop alive for next sentences
            else:
                # Even on synthesis failure/empty audio we must still emit an
                # end-of-line marker: the frontend StrictLineAudioQueue only
                # marks a line `done` on `line_audio_end`, and without it the
                # line would hold the queue head forever → "TTS chỉ đọc 1 câu
                # rồi dừng".
                await _safe_json(browser_ws, {
                    "type": "audio_chunk_meta",
                    "line_id": line_id,
                    "byte_length": 0,
                    "line_audio_end": True,
                })
            async with state.cv:
                state.next_seq = seq + 1
                state.cv.notify_all()

    # Global seq counter per direction — must be incremented atomically under cv.
    direction_seq: dict[str, int] = {d: 0 for d in direction_voices}
    in_flight: dict[str, int] = {d: 0 for d in direction_voices}
    # Barge epoch per direction: bumped whenever a barge-in resets the ordered
    # playback state so in-flight synthesis results from before the barge can
    # be recognized as stale and dropped.
    direction_epoch: dict[str, int] = {d: 0 for d in direction_voices}

    def enqueue_line(direction: str, text: str, line_id: int) -> None:
        """Queue exactly one synthesis job for exactly one rendered line.

        The frontend audio queue advances by ``line_id``. Grouping multiple
        rendered lines into one audio item and labelling it with the final ID
        leaves the earlier registered line with no audio/end marker, so the
        queue blocks after the first spoken item. Keep this mapping 1:1.
        """
        if not text.strip():
            return
        seq = direction_seq[direction]
        direction_seq[direction] += 1
        in_flight[direction] += 1
        asyncio.create_task(
            synthesize_one(
                direction, text, seq, line_id, states[direction], direction_epoch[direction]
            )
        )

    controllers = {
        direction: asyncio.create_task(playback_loop(direction, state))
        for direction, state in states.items()
    }

    try:
        while True:
            data = await tts_queue.get()
            if data is TTS_NONE:
                # Wait for all in-flight synthesis tasks to finish and
                # playback controllers to drain their pending queues.
                for direction, state in states.items():
                    async with state.cv:
                        await state.cv.wait_for(
                            lambda d=direction, s=state: in_flight[d] == 0 and not s.pending
                        )
                await _safe_json(browser_ws, {"session_done": True})
                return
            kind = data[0]
            if kind == TTS_BARGE:
                for direction, state in states.items():
                    async with state.cv:
                        for _, fut in list(state.pending.values()):
                            fut.cancel()
                        state.pending.clear()
                        state.next_seq = 0
                        direction_seq[direction] = 0
                        # Bump the epoch so in-flight synthesize_one tasks
                        # started before the barge drop their audio instead of
                        # registering stale seqs into the fresh pending map.
                        direction_epoch[direction] += 1
                        state.cv.notify_all()
                    # Wait for pre-barge in-flight synthesis tasks to wind
                    # down: they decrement in_flight under cv, so the TTS_NONE
                    # drain below never sees a false "busy" state.
                    async with state.cv:
                        await state.cv.wait_for(
                            lambda d=direction: in_flight[d] == 0
                        )
                my_epoch = tts_state["barge_epoch"]
                continue
            if my_epoch != tts_state["barge_epoch"]:
                continue
            if kind == TTS_TEXT:
                _, payload, direction, line_id = data
                if direction in states:
                    enqueue_line(direction, payload, line_id)
            elif kind == TTS_END:
                # Each TTS_TEXT is already a complete display/audio line.
                # The marker only preserves compatibility with the queue protocol.
                continue
    finally:
        for task in controllers.values():
            task.cancel()


async def _run_synthesize(
    browser_ws: WebSocket,
    provider: TTSProviderBase | None,
    provider_id: str,
    voice_id: str,
    direction: str,
    text: str,
    fallback_synthesize: Callable[[str, str, str], Awaitable[bytes]] = synthesize_soniox_text,
) -> bytes:
    cache_hit = False
    used_provider = provider_id
    estimated_cost = 0.0

    audio = tts_cache.get(text, voice_id, provider_id)
    if audio is not None:
        cache_hit = True
    else:
        try:
            if provider is None:
                raise ValueError(f"Unknown TTS provider: {provider_id}")
            chunks: list[bytes] = []
            async for chunk in provider.synthesize_stream(text, voice_id, direction):
                chunks.append(chunk)
            audio = b"".join(chunks)
            if not audio:
                raise RuntimeError(f"{provider_id} returned no audio")
            # Trim leading/trailing silence so consecutive utterances join
            # seamlessly — this is the main cause of jerky, stuttering speech.
            audio = _trim_pcm_silence(audio)
            tts_cache.set(text, voice_id, provider_id, audio)
            estimated_cost = provider.estimate_cost(len(text))
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            log.warning("tts_provider_fallback", provider=provider_id, reason=reason)
            await _safe_json(browser_ws, {
                "tts_fallback": {
                    "from_provider": provider_id,
                    "to_provider": "soniox",
                    "reason": reason[:240],
                }
            })
            used_provider = "soniox"
            audio = tts_cache.get(text, "Maya", "soniox")
            if audio is None:
                try:
                    audio = await fallback_synthesize(text, "Maya", direction)
                    tts_cache.set(text, "Maya", "soniox", audio)
                except Exception as fallback_exc:
                    await _safe_json(browser_ws, {
                        "tts_error": {
                            "provider_id": provider_id,
                            "message": str(fallback_exc)[:240],
                        }
                    })
                    return b""

    await _safe_json(browser_ws, {
        "tts_usage": {
            "provider_id": used_provider,
            "voice_id": voice_id,
            "characters": len(text),
            "estimated_cost_usd": 0.0 if cache_hit else estimated_cost,
            "cache_hit": cache_hit,
        }
    })
    return audio


def _trim_pcm_silence(audio: bytes, sample_rate: int = 24000, keep_ms: int = 20) -> bytes:
    """Trim leading/trailing silence from a PCM s16le stream.

    Consecutive TTS utterances each carry engine-added silence at both ends.
    When played back-to-back those pauses stack into a jerky, stuttering
    rhythm. We cut all but a tiny 20 ms cushion on each side so sentences
    flow into each other smoothly while still breathing naturally.
    """
    if len(audio) < 4:
        return audio
    samples = memoryview(audio).cast("h")
    keep = max(0, int(sample_rate * keep_ms / 1000))

    def _is_silence(i: int) -> bool:
        # Amplitude below ~2.5% of full scale on a small window.
        start = max(0, i - 2)
        end = min(len(samples), i + 3)
        window = samples[start:end]
        return max((abs(s) for s in window), default=0) < 800

    # Scan start
    start_idx = 0
    while start_idx < len(samples) - keep:
        if not _is_silence(start_idx):
            break
        start_idx += 1
    start_idx = max(0, start_idx - keep)

    # Scan end
    end_idx = len(samples)
    while end_idx > start_idx + keep:
        if not _is_silence(end_idx - 1):
            break
        end_idx -= 1
    end_idx = min(len(samples), end_idx + keep)

    if end_idx <= start_idx:
        return audio
    return bytes(memoryview(samples)[start_idx:end_idx].cast("B"))
