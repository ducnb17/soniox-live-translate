"""Local faster-whisper GPU STT adapter.

The browser already sends signed 16-bit mono PCM at 16 kHz.  This adapter
collects one voiced utterance, detects a short silence, then uses the user's
local GPU server's ``POST /stt`` API.  Faster-whisper itself is not a true
partial-token streaming engine, so this emits final text per detected
utterance rather than pretending to be a low-latency cloud stream.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import struct
import wave
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider

_SAMPLE_RATE = 16_000
_SAMPLE_WIDTH = 2
# AudioWorklet posts 128 frames at a time (~8 ms).  Require a modest sustained
# signal to begin; use 0.75s of silence to close an utterance.
_ENERGY_THRESHOLD = 350
_END_SILENCE_SECONDS = 0.75
_MIN_SPEECH_SECONDS = 0.20
_MAX_UTTERANCE_SECONDS = 12.0


def _endpoint() -> str:
    return os.environ.get("LOCAL_WHISPER_STT_URL", "http://localhost:8767").rstrip("/")


def _pcm_energy(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    usable = len(pcm) - (len(pcm) % 2)
    samples = struct.unpack(f"<{usable // 2}h", pcm[:usable])
    return sum(abs(sample) for sample in samples) / len(samples) if samples else 0.0


def _seconds(pcm: bytes | bytearray) -> float:
    return len(pcm) / (_SAMPLE_RATE * _SAMPLE_WIDTH)


def _as_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(_SAMPLE_WIDTH)
        writer.setframerate(_SAMPLE_RATE)
        writer.writeframes(pcm)
    return output.getvalue()


@register_provider
class LocalWhisperGPUProvider(STTProviderBase):
    """faster-whisper Large-v3 through the local C246 GPU bridge."""

    def __init__(self, api_key: str | None = None) -> None:
        # Kept for the common provider constructor contract; local server needs none.
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{_endpoint()}/health")
                response.raise_for_status()
                payload = response.json()
            if payload.get("ok") and payload.get("stt_gpu"):
                return True, "OK — faster-whisper Large-v3 (local GPU)"
            return False, f"Local Whisper server is not ready: {payload}"
        except Exception as exc:
            return False, f"Cannot connect to local Whisper service at {_endpoint()}: {exc}"

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        **options: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Hints in the existing Soniox pipeline describe translation targets,
        # not the source language. Let Whisper auto-detect source speech so
        # English→Vietnamese and Vietnamese→English both work correctly.
        language = None
        speech = bytearray()
        silence = bytearray()

        async def transcribe(pcm: bytes) -> dict[str, Any] | None:
            if _seconds(pcm) < _MIN_SPEECH_SECONDS:
                return None
            request = {
                "audio_base64": base64.b64encode(_as_wav(pcm)).decode("ascii"),
                "language": language,
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(f"{_endpoint()}/stt", json=request)
                response.raise_for_status()
                result = response.json()
            text = str(result.get("text") or "").strip()
            if not text:
                return None
            return {
                "tokens": [{
                    "text": text,
                    "language": result.get("language") or language or "",
                    "translation_status": "none",
                    "speaker": None,
                }],
                "finished": False,
            }

        async for incoming in audio_stream:
            if not incoming:
                continue
            pcm = bytes(incoming[:len(incoming) - (len(incoming) % 2)])
            if not pcm:
                continue
            voiced = _pcm_energy(pcm) >= _ENERGY_THRESHOLD
            if voiced:
                speech.extend(silence)
                silence.clear()
                speech.extend(pcm)
            elif speech:
                silence.extend(pcm)

            ready = speech and (
                _seconds(silence) >= _END_SILENCE_SECONDS
                or _seconds(speech) + _seconds(silence) >= _MAX_UTTERANCE_SECONDS
            )
            if ready:
                result = await transcribe(bytes(speech))
                if result:
                    yield result
                yield {"tokens": [{"text": "<end>", "language": language or ""}], "finished": False}
                speech.clear()
                silence.clear()

        if speech:
            result = await transcribe(bytes(speech))
            if result:
                yield result
            yield {"tokens": [{"text": "<end>", "language": language or ""}], "finished": False}
        yield {"tokens": [], "finished": True}

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="local_whisper_gpu",
            name="Whisper Large-v3 (local GPU)",
            description="Offline Vietnamese/multilingual transcription using faster-whisper Large-v3 on the local RTX GPU. Final text is emitted after a short speech pause.",
            requires_api_key=False,
            supports_streaming=True,
            supports_realtime_translation=False,
            tier="free",
            pricing_url="https://github.com/SYSTRAN/faster-whisper",
            approximate_cost_per_hour=0.0,
        )


class LocalWhisperSttStream:
    """Queue-backed WebSocket-shaped bridge for ``handle_stt``."""

    def __init__(self, provider: LocalWhisperGPUProvider, language_hints: list[str] | None = None) -> None:
        self._provider = provider
        self._language_hints = language_hints or []
        self._audio_in: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)
        self._tokens_out: asyncio.Queue[str | None | BaseException] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def open(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async def audio_gen() -> AsyncIterator[bytes]:
            while True:
                item = await self._audio_in.get()
                if item is None or item == b"":
                    break
                yield item
        try:
            async for message in self._provider.transcribe_stream(audio_gen(), language_hints=self._language_hints):
                await self._tokens_out.put(json.dumps(message))
        except Exception as exc:
            await self._tokens_out.put(exc)
        finally:
            await self._tokens_out.put(None)

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, bytes):
            await self._audio_in.put(data if data else None)

    async def recv(self) -> str:
        item = await self._tokens_out.get()
        if item is None:
            raise ConnectionError("Local Whisper stream ended")
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.close_code = 1000
        self.close_reason = "closed"
        try:
            self._audio_in.put_nowait(None)
        except asyncio.QueueFull:
            pass
