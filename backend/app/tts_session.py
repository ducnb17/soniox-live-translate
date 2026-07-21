"""Independent browser TTS WebSocket session.

This module deliberately has no STT imports.  A session owns its command
queue, synthesis task, cancellation epoch and browser writes, so stopping TTS
cannot close or otherwise mutate an STT connection.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from .config_store import get_tts_api_key
from .logging_config import get_logger
from .tts_provider import (
    TTSProviderBase,
    get_provider,
    tts_cache,
)
from .tts import synthesize_soniox_text

log = get_logger("tts_session")


@dataclass(frozen=True)
class SpeakRequest:
    request_id: str
    line_id: int
    text: str
    lang: str
    voice: str
    epoch: int


ProviderFactory = Callable[[str, str | None], TTSProviderBase | None]
FallbackSynthesizer = Callable[[str, str, str], Awaitable[bytes]]


class TtsSessionController:
    """Own one independent TTS command stream and its cancellation boundary."""

    def __init__(
        self,
        browser_ws: WebSocket,
        provider_factory: ProviderFactory = get_provider,
        fallback_synthesizer: FallbackSynthesizer | None = None,
    ) -> None:
        self.browser_ws = browser_ws
        self.provider_factory = provider_factory
        self.fallback_synthesizer = fallback_synthesizer or synthesize_soniox_text
        self.queue: asyncio.Queue[SpeakRequest | None] = asyncio.Queue()
        self.enabled = False
        self.epoch = 0
        self.provider_id = "soniox"
        self.default_voice = "Maya"
        self.closed = False
        self._cleanup_complete = False
        self._worker: asyncio.Task[None] | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._cancel_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._seen: set[tuple[int, str]] = set()

    async def run(self) -> None:
        self._worker = asyncio.create_task(self._worker_loop())
        await self._send_state("off")
        try:
            while True:
                message = await self.browser_ws.receive_json()
                await self.handle_command(message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await self.close()

    async def handle_command(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "configure":
            await self._configure(message)
        elif kind == "speak":
            await self._enqueue_speak(message)
        elif kind == "cancel_all":
            await self.cancel_all(message.get("epoch"))
        else:
            await self._send_json({
                "type": "tts_error",
                "epoch": self.epoch,
                "message": f"Unknown TTS command: {kind}",
            })

    async def _configure(self, message: dict[str, Any]) -> None:
        requested_epoch = int(message.get("epoch", self.epoch))
        if requested_epoch < self.epoch:
            return
        self.epoch = requested_epoch
        self.provider_id = str(message.get("provider") or "soniox")
        self.default_voice = str(message.get("voice") or "Maya")
        self.enabled = bool(message.get("enabled", True))
        if not self.enabled:
            await self.cancel_all(self.epoch)
            return

        # Every registered provider implements ``synthesize_stream``.  For
        # providers such as Google/OpenAI the implementation yields a single
        # complete PCM response rather than true incremental audio, which is
        # still valid for this session protocol.  ``supports_streaming`` is a
        # latency/capability hint for the UI, not an admission check.
        provider = self._provider()
        if provider is None:
            self.enabled = False
            await self._send_json({
                "type": "tts_error",
                "epoch": self.epoch,
                "provider": self.provider_id,
                "provider_id": self.provider_id,
                "message": f"Unknown TTS provider: {self.provider_id}",
                "recoverable": True,
            })
            await self._send_state("error")
            return
        await self._send_state("on")

    async def _enqueue_speak(self, message: dict[str, Any]) -> None:
        request_epoch = int(message.get("epoch", -1))
        if not self.enabled or request_epoch != self.epoch:
            return
        text = str(message.get("text") or "").strip()
        request_id = str(message.get("request_id") or "")
        if not text or not request_id:
            return
        dedup_key = (request_epoch, request_id)
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)
        await self.queue.put(SpeakRequest(
            request_id=request_id,
            line_id=int(message.get("line_id", 0)),
            text=text,
            lang=str(message.get("direction") or message.get("lang") or "en"),
            voice=str(message.get("voice") or self.default_voice),
            epoch=request_epoch,
        ))

    async def cancel_all(self, requested_epoch: Any = None) -> None:
        """Cancel active synthesis and atomically invalidate queued audio."""
        async with self._cancel_lock:
            next_epoch = self.epoch + 1
            if requested_epoch is not None:
                next_epoch = max(next_epoch, int(requested_epoch))
            self.epoch = next_epoch
            active = self._active_task
            self._active_task = None
            if active is not None and not active.done():
                active.cancel()
                with suppress(asyncio.CancelledError):
                    await active
            self._drain_queue()
            self._seen.clear()
        await self._send_state("on" if self.enabled else "off")

    async def close(self) -> None:
        if self._cleanup_complete:
            return
        self._cleanup_complete = True
        self.closed = True
        self.enabled = False
        active = self._active_task
        if active is not None and not active.done():
            active.cancel()
            with suppress(asyncio.CancelledError):
                await active
        self._drain_queue()
        worker = self._worker
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    async def _worker_loop(self) -> None:
        while True:
            request = await self.queue.get()
            try:
                if request is None:
                    return
                if not self.enabled or request.epoch != self.epoch:
                    continue
                task = asyncio.create_task(self._synthesize(request))
                self._active_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    worker = asyncio.current_task()
                    if worker is not None and worker.cancelling():
                        raise
                    # ``cancel_all`` cancelled only the active synthesis;
                    # this queue worker remains reusable for the same socket.
                finally:
                    if self._active_task is task:
                        self._active_task = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if request is not None and request.epoch == self.epoch:
                    log.warning(
                        "tts_request_failed",
                        provider=self.provider_id,
                        request_id=request.request_id,
                        error=str(exc),
                    )
                    await self._send_json({
                        "type": "tts_error",
                        "provider": self.provider_id,
                        "provider_id": self.provider_id,
                        "request_id": request.request_id,
                        "line_id": request.line_id,
                        "epoch": request.epoch,
                        "message": str(exc),
                        "recoverable": True,
                    })
            finally:
                self.queue.task_done()

    async def _synthesize(self, request: SpeakRequest) -> None:
        used_provider = self.provider_id
        voice = request.voice
        cache_hit = False
        estimated_cost = 0.0
        audio = tts_cache.get(request.text, voice, used_provider)
        if audio is not None:
            cache_hit = True
        elif self.provider_id == "soniox":
            audio = await self.fallback_synthesizer(
                request.text, voice, request.lang
            )
            tts_cache.set(request.text, voice, used_provider, audio)
        else:
            provider = self._provider()
            if provider is None:
                raise RuntimeError(f"Unknown TTS provider: {self.provider_id}")
            try:
                chunks = [
                    chunk
                    async for chunk in provider.synthesize_stream(
                        request.text, voice, request.lang
                    )
                ]
                audio = b"".join(chunks)
                if not audio:
                    raise RuntimeError("TTS provider returned no audio")
                tts_cache.set(request.text, voice, used_provider, audio)
                estimated_cost = provider.estimate_cost(len(request.text))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._send_json({
                    "type": "tts_fallback",
                    "request_id": request.request_id,
                    "line_id": request.line_id,
                    "epoch": request.epoch,
                    "from_provider": self.provider_id,
                    "to_provider": "soniox",
                    "reason": str(exc)[:240],
                })
                used_provider = "soniox"
                voice = "Maya"
                audio = tts_cache.get(request.text, voice, used_provider)
                cache_hit = audio is not None
                if audio is None:
                    audio = await self.fallback_synthesizer(
                        request.text, voice, request.lang
                    )
                    tts_cache.set(request.text, voice, used_provider, audio)

        if self.closed or not self.enabled or request.epoch != self.epoch:
            return
        await self._send_audio(request, audio, True)
        await self._send_json({
            "type": "line_audio_complete",
            "request_id": request.request_id,
            "line_id": request.line_id,
            "epoch": request.epoch,
        })
        await self._send_json({
            "type": "tts_usage",
            "request_id": request.request_id,
            "line_id": request.line_id,
            "epoch": request.epoch,
            "provider_id": used_provider,
            "voice_id": voice,
            "characters": len(request.text),
            "estimated_cost_usd": 0.0 if cache_hit else estimated_cost,
            "cache_hit": cache_hit,
        })

    async def _send_audio(
        self, request: SpeakRequest, audio: bytes, line_audio_end: bool
    ) -> None:
        async with self._send_lock:
            if self.closed or not self.enabled or request.epoch != self.epoch:
                return
            try:
                await self.browser_ws.send_json({
                    "type": "audio_chunk_meta",
                    "request_id": request.request_id,
                    "line_id": request.line_id,
                    "epoch": request.epoch,
                    "byte_length": len(audio),
                    "line_audio_end": line_audio_end,
                })
                await self.browser_ws.send_bytes(audio)
            except (RuntimeError, WebSocketDisconnect):
                self.closed = True

    async def _send_state(self, state: str) -> None:
        await self._send_json({
            "type": "tts_state",
            "state": state,
            "enabled": self.enabled,
            "epoch": self.epoch,
            "provider_id": self.provider_id,
        })

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self._send_lock:
            if self.closed:
                return
            try:
                await self.browser_ws.send_json(payload)
            except (RuntimeError, WebSocketDisconnect):
                self.closed = True

    def _provider(self) -> TTSProviderBase | None:
        return self.provider_factory(
            self.provider_id, get_tts_api_key(self.provider_id)
        )

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                return


async def serve_tts_websocket(browser_ws: WebSocket) -> None:
    await browser_ws.accept()
    await TtsSessionController(browser_ws).run()
