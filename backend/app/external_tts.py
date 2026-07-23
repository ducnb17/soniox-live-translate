"""Queue consumer for non-Soniox TTS providers with Soniox fallback."""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from .logging_config import get_logger
from .stt import TTS_END, TTS_NONE, TTS_TEXT
from .tts import TTS_BARGE, synthesize_soniox_text
from .tts_provider import TTSProviderBase

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
    """Synthesize each stable phrase immediately and forward PCM to the UI."""
    my_epoch = tts_state["barge_epoch"]

    async def synthesize_line(direction: str, text: str, line_id: int) -> None:
        nonlocal my_epoch
        if not text:
            return
        voice_id = direction_voices[direction]
        started_epoch = tts_state["barge_epoch"]
        used_provider = provider_id
        estimated_cost = 0.0
        audio_bytes = 0
        provider_audio_started = False
        pending = b""

        async def send_audio(chunk: bytes, *, line_audio_end: bool) -> None:
            nonlocal audio_bytes
            if started_epoch != tts_state["barge_epoch"] or not chunk:
                return
            await browser_ws.send_json(
                {
                    "type": "audio_chunk_meta",
                    "line_id": line_id,
                    "byte_length": len(chunk),
                    "line_audio_end": line_audio_end,
                }
            )
            await browser_ws.send_bytes(chunk)
            audio_bytes += len(chunk)

        try:
            if provider is None:
                raise ValueError(f"Unknown TTS provider: {provider_id}")
            iterator = provider.synthesize_stream(text, voice_id, direction).__aiter__()
            pending = await anext(iterator)
            while True:
                try:
                    next_chunk = await anext(iterator)
                except StopAsyncIteration:
                    await send_audio(pending, line_audio_end=True)
                    provider_audio_started = provider_audio_started or bool(pending)
                    break
                await send_audio(pending, line_audio_end=False)
                provider_audio_started = provider_audio_started or bool(pending)
                pending = next_chunk
            if not provider_audio_started:
                raise RuntimeError(f"{provider_id} returned no audio")
            estimated_cost = provider.estimate_cost(len(text))
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            if provider_audio_started:
                await send_audio(pending, line_audio_end=True)
                await _safe_json(browser_ws, {
                    "tts_error": {
                        "provider_id": provider_id,
                        "message": reason[:240],
                    }
                })
                return
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
            try:
                audio = await fallback_synthesize(text, voice_id, direction)
            except Exception as fallback_exc:
                await _safe_json(browser_ws, {
                    "tts_error": {
                        "provider_id": provider_id,
                        "message": str(fallback_exc)[:240],
                    }
                })
                return
            await send_audio(audio, line_audio_end=True)

        if started_epoch != tts_state["barge_epoch"]:
            return
        if audio_bytes:
            await _safe_json(browser_ws, {
                "tts_usage": {
                    "provider_id": used_provider,
                    "voice_id": voice_id,
                    "characters": len(text),
                    "estimated_cost_usd": estimated_cost,
                }
            })
        my_epoch = tts_state["barge_epoch"]

    while True:
        data = await tts_queue.get()
        if data is TTS_NONE:
            await _safe_json(browser_ws, {"session_done": True})
            return
        kind = data[0]
        if kind == TTS_BARGE:
            my_epoch = tts_state["barge_epoch"]
            continue
        if my_epoch != tts_state["barge_epoch"]:
            continue
        if kind == TTS_TEXT:
            _, payload, direction, line_id = data
            if direction in direction_voices:
                await synthesize_line(direction, payload.strip(), line_id)
        elif kind == TTS_END:
            # Phrases are already synthesized on arrival. The marker only
            # closes native streaming providers and is a no-op here.
            continue
