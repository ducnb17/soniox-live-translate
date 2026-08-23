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

    async def synthesize_one(direction: str, text: str, seq: int, line_id: int, state: DirectionState) -> None:
        # Synthesize FIRST, then register the result — avoids race where
        # playback_loop awaits a future that is not yet scheduled.
        try:
            audio = await _run_synthesize(browser_ws, provider, provider_id, direction_voices[direction], direction, text, fallback_synthesize)
        except Exception:
            audio = b""
        fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        fut.set_result(audio)
        async with state.cv:
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
            async with state.cv:
                state.next_seq = seq + 1
                state.cv.notify_all()

    # Global seq counter per direction — must be incremented atomically under cv.
    direction_seq: dict[str, int] = {d: 0 for d in direction_voices}
    in_flight: dict[str, int] = {d: 0 for d in direction_voices}

    async def handle_text(direction: str, text: str, line_id: int) -> None:
        state = states[direction]
        async with state.cv:
            seq = direction_seq[direction]
            direction_seq[direction] += 1
            in_flight[direction] += 1
        asyncio.create_task(synthesize_one(direction, text, seq, line_id, state))

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
                        in_flight[direction] = 0
                        state.cv.notify_all()
                my_epoch = tts_state["barge_epoch"]
                continue
            if my_epoch != tts_state["barge_epoch"]:
                continue
            if kind == TTS_TEXT:
                _, payload, direction, line_id = data
                if direction in states:
                    await handle_text(direction, payload, line_id)
            elif kind == TTS_END:
                pass
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
