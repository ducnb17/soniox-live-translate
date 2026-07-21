"""Queue consumer for non-Soniox TTS providers with cache and fallback."""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from .logging_config import get_logger
from .tts import TTS_END, TTS_NONE, TTS_TEXT
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
    """Synthesize completed translated utterances and forward PCM to the UI."""
    buffers: dict[str, list[tuple[str, int]]] = {
        direction: [] for direction in direction_voices
    }
    my_epoch = tts_state["barge_epoch"]

    async def synthesize_line(direction: str, text: str, line_id: int) -> None:
        nonlocal my_epoch
        if not text:
            return
        voice_id = direction_voices[direction]
        started_epoch = tts_state["barge_epoch"]
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
                chunks = [chunk async for chunk in provider.synthesize_stream(text, voice_id, direction)]
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
                voice_id = fallback_voice
                audio = tts_cache.get(text, voice_id, "soniox")
                cache_hit = audio is not None
                if audio is None:
                    try:
                        audio = await fallback_synthesize(text, voice_id, direction)
                        tts_cache.set(text, voice_id, "soniox", audio)
                    except Exception as fallback_exc:
                        await _safe_json(browser_ws, {
                            "tts_error": {
                                "provider_id": provider_id,
                                "message": str(fallback_exc)[:240],
                            }
                        })
                        return

        if started_epoch != tts_state["barge_epoch"]:
            return
        if audio:
            await browser_ws.send_json(
                {
                    "type": "audio_chunk_meta",
                    "line_id": line_id,
                    "byte_length": len(audio),
                    "line_audio_end": True,
                }
            )
            await browser_ws.send_bytes(audio)
            await _safe_json(browser_ws, {
                "tts_usage": {
                    "provider_id": used_provider,
                    "voice_id": voice_id,
                    "characters": len(text),
                    "estimated_cost_usd": 0.0 if cache_hit else estimated_cost,
                    "cache_hit": cache_hit,
                }
            })
        my_epoch = tts_state["barge_epoch"]

    async def synthesize_direction(direction: str) -> None:
        parts = buffers.get(direction, [])
        buffers[direction] = []
        grouped_lines: list[tuple[int, list[str]]] = []
        for text, line_id in parts:
            if grouped_lines and grouped_lines[-1][0] == line_id:
                grouped_lines[-1][1].append(text)
            else:
                grouped_lines.append((line_id, [text]))
        for line_id, line_parts in grouped_lines:
            await synthesize_line(direction, "".join(line_parts).strip(), line_id)

    while True:
        data = await tts_queue.get()
        if data is TTS_NONE:
            await _safe_json(browser_ws, {"session_done": True})
            return
        kind = data[0]
        if kind == TTS_BARGE:
            for direction in buffers:
                buffers[direction] = []
            my_epoch = tts_state["barge_epoch"]
            continue
        if my_epoch != tts_state["barge_epoch"]:
            continue
        if kind == TTS_TEXT:
            _, payload, direction, line_id = data
            if direction in buffers:
                buffers[direction].append((payload, line_id))
        elif kind == TTS_END:
            _, direction = data
            targets = [direction] if direction else list(buffers)
            for target in targets:
                if target in buffers:
                    await synthesize_direction(target)
