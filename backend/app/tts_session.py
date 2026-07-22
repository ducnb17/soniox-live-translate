"""Independent browser TTS WebSocket session.

This module deliberately has no STT imports.  A session owns its command
queue, synthesis task, cancellation epoch and browser writes, so stopping TTS
cannot close or otherwise mutate an STT connection.
"""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from . import config as runtime_config
from .config import TTS_KEEPALIVE_INTERVAL
from .config_store import get_tts_api_key
from .logging_config import get_logger
from .tts_provider import (
    TTSProviderBase,
    get_provider,
    tts_cache,
)
from .tts import get_tts_config, synthesize_soniox_text

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
RealtimeConnector = Callable[..., Awaitable[Any]]


@dataclass
class RealtimeRequest:
    request_id: str
    line_id: int
    direction: str
    voice: str
    epoch: int
    characters: int = 0
    audio_ended: bool = False
    text_ended: bool = False


class SonioxRealtimeSession:
    """One persistent Soniox TTS WebSocket with one stream per utterance."""

    def __init__(
        self,
        owner: "TtsSessionController",
        connector: RealtimeConnector,
    ) -> None:
        self.owner = owner
        self.connector = connector
        self.ws: Any = None
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._receiver: asyncio.Task[None] | None = None
        self._keepalive: asyncio.Task[None] | None = None
        self._stream_counter = 0
        self._prewarmed: dict[str, str] = {}
        self._requests_by_stream: dict[str, RealtimeRequest | None] = {}
        self._stream_by_request: dict[str, str] = {}

    async def start(self, directions: dict[str, str]) -> None:
        self.ws = await self.connector(
            runtime_config.TTS_URL,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
        )
        self._receiver = asyncio.create_task(self._receive_loop())
        self._keepalive = asyncio.create_task(self._keepalive_loop())
        for direction, voice in directions.items():
            if direction:
                await self._prewarm(direction, voice)

    async def send_text(self, request: RealtimeRequest, text: str) -> None:
        if self.closed or self.ws is None:
            raise RuntimeError("Soniox real-time TTS is not connected")
        stream_id = self._stream_by_request.get(request.request_id)
        if stream_id is None:
            stream_id = self._prewarmed.pop(request.direction, None)
            if stream_id is None:
                stream_id = await self._open_stream(request.direction, request.voice)
            self._requests_by_stream[stream_id] = request
            self._stream_by_request[request.request_id] = stream_id
        current = self._requests_by_stream.get(stream_id)
        if current is None or current.epoch != request.epoch:
            return
        current.characters += len(text)
        await self._send({
            "stream_id": stream_id,
            "text": text,
            "text_end": False,
        })

    async def end_text(self, request_id: str) -> None:
        stream_id = self._stream_by_request.get(request_id)
        if stream_id is None:
            return
        request = self._requests_by_stream.get(stream_id)
        if request is None or request.text_ended:
            return
        request.text_ended = True
        await self._send({
            "stream_id": stream_id,
            "text": "",
            "text_end": True,
        })

    async def close(self) -> None:
        if self.closed:
            return
        if self.ws is not None:
            for stream_id in list(self._requests_by_stream):
                with suppress(Exception):
                    await self._send({"stream_id": stream_id, "cancel": True})
        self.closed = True
        for task in (self._receiver, self._keepalive):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if self.ws is not None:
            with suppress(Exception):
                await self.ws.close()
        self._requests_by_stream.clear()
        self._stream_by_request.clear()
        self._prewarmed.clear()

    async def _prewarm(self, direction: str, voice: str) -> None:
        if direction in self._prewarmed:
            return
        stream_id = await self._open_stream(direction, voice, prefix="prewarm")
        self._prewarmed[direction] = stream_id

    async def _open_stream(
        self, direction: str, voice: str, *, prefix: str = "utterance"
    ) -> str:
        self._stream_counter += 1
        stream_id = f"{prefix}-{self.owner.epoch}-{self._stream_counter}-{direction}"
        await self._send(get_tts_config(stream_id, voice, direction))
        self._requests_by_stream[stream_id] = None
        return stream_id

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            if self.closed or self.ws is None:
                return
            await self.ws.send(json.dumps(payload))

    async def _receive_loop(self) -> None:
        try:
            while True:
                data = json.loads(await self.ws.recv())
                stream_id = str(data.get("stream_id") or "")
                request = self._requests_by_stream.get(stream_id)
                if data.get("error_code") is not None:
                    if request is not None:
                        await self.owner._send_realtime_audio(request, b"", True)
                        await self.owner._send_json({
                            "type": "tts_error",
                            "provider": "soniox",
                            "provider_id": "soniox",
                            "request_id": request.request_id,
                            "line_id": request.line_id,
                            "epoch": request.epoch,
                            "message": str(data.get("error_message") or data["error_code"]),
                            "recoverable": True,
                        })
                        self._forget_stream(stream_id)
                    else:
                        await self.owner._realtime_failed(
                            str(data.get("error_message") or data["error_code"])
                        )
                        return
                    continue

                audio_b64 = data.get("audio")
                if audio_b64 and request is not None:
                    audio = base64.b64decode(audio_b64)
                    audio_end = bool(data.get("audio_end"))
                    request.audio_ended = request.audio_ended or audio_end
                    await self.owner._send_realtime_audio(request, audio, audio_end)

                if data.get("terminated"):
                    if request is not None:
                        if not request.audio_ended:
                            await self.owner._send_realtime_audio(request, b"", True)
                        await self.owner._send_realtime_complete(request)
                    self._forget_stream(stream_id)
                    if (
                        request is not None
                        and request.direction not in self._prewarmed
                        and not any(
                            other is not None and other.direction == request.direction
                            for other in self._requests_by_stream.values()
                        )
                    ):
                        # Keep the next utterance warm, matching Soniox's
                        # reference pipeline without opening extra streams
                        # while another utterance in this direction is active.
                        await self._prewarm(request.direction, request.voice)
        except asyncio.CancelledError:
            raise
        except (websockets.ConnectionClosed, RuntimeError) as exc:
            if not self.closed:
                await self.owner._realtime_failed(str(exc))
        except Exception as exc:
            if not self.closed:
                await self.owner._realtime_failed(str(exc))

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(TTS_KEEPALIVE_INTERVAL)
                await self._send({"keep_alive": True})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.closed:
                await self.owner._realtime_failed(str(exc))

    def _forget_stream(self, stream_id: str) -> None:
        request = self._requests_by_stream.pop(stream_id, None)
        if request is not None:
            self._stream_by_request.pop(request.request_id, None)
        for direction, prewarmed_id in list(self._prewarmed.items()):
            if prewarmed_id == stream_id:
                self._prewarmed.pop(direction, None)


class TtsSessionController:
    """Own one independent TTS command stream and its cancellation boundary."""

    def __init__(
        self,
        browser_ws: WebSocket,
        provider_factory: ProviderFactory = get_provider,
        fallback_synthesizer: FallbackSynthesizer | None = None,
        realtime_connector: RealtimeConnector = websockets.connect,
    ) -> None:
        self.browser_ws = browser_ws
        self.provider_factory = provider_factory
        self.fallback_synthesizer = fallback_synthesizer or synthesize_soniox_text
        self.realtime_connector = realtime_connector
        self.queue: asyncio.Queue[SpeakRequest | None] = asyncio.Queue()
        self.enabled = False
        self.epoch = 0
        self.provider_id = "soniox"
        self.default_voice = "Maya"
        self.realtime_streaming = False
        self._realtime_directions: dict[str, str] = {}
        self._realtime: SonioxRealtimeSession | None = None
        self.closed = False
        self._cleanup_complete = False
        self._worker: asyncio.Task[None] | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._cancel_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._seen: set[tuple[int, str]] = set()
        self._seen_chunks: set[tuple[int, str, int]] = set()

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
        elif kind == "stream_text":
            await self._stream_text(message)
        elif kind == "stream_end":
            await self._stream_end(message)
        elif kind == "cancel_all":
            await self.cancel_all(
                message.get("epoch"), restart_realtime=bool(message.get("restart", True))
            )
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
        self.realtime_streaming = bool(message.get("realtime_streaming"))
        self._realtime_directions = self._directions_from_config(message)
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
        if self._realtime is not None:
            await self._realtime.close()
            self._realtime = None
        if self.provider_id == "soniox" and self.realtime_streaming:
            try:
                await self._start_realtime()
            except Exception as exc:
                self.enabled = False
                await self._send_json({
                    "type": "tts_error",
                    "epoch": self.epoch,
                    "provider": "soniox",
                    "provider_id": "soniox",
                    "message": f"Soniox real-time TTS connection failed: {exc}",
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

    async def _stream_text(self, message: dict[str, Any]) -> None:
        request_epoch = int(message.get("epoch", -1))
        if (
            not self.enabled
            or request_epoch != self.epoch
            or self.provider_id != "soniox"
            or not self.realtime_streaming
            or self._realtime is None
        ):
            return
        text = str(message.get("text") or "")
        request_id = str(message.get("request_id") or "")
        sequence = int(message.get("sequence", 0))
        if not text or not request_id or sequence <= 0:
            return
        dedup_key = (request_epoch, request_id, sequence)
        if dedup_key in self._seen_chunks:
            return
        self._seen_chunks.add(dedup_key)
        request = RealtimeRequest(
            request_id=request_id,
            line_id=int(message.get("line_id", 0)),
            direction=str(message.get("direction") or message.get("lang") or "en"),
            voice=str(message.get("voice") or self.default_voice),
            epoch=request_epoch,
        )
        try:
            await self._realtime.send_text(request, text)
        except Exception as exc:
            await self._realtime_failed(str(exc))

    async def _stream_end(self, message: dict[str, Any]) -> None:
        request_epoch = int(message.get("epoch", -1))
        request_id = str(message.get("request_id") or "")
        if (
            not request_id
            or not self.enabled
            or request_epoch != self.epoch
            or self._realtime is None
        ):
            return
        try:
            await self._realtime.end_text(request_id)
        except Exception as exc:
            await self._realtime_failed(str(exc))

    async def cancel_all(
        self, requested_epoch: Any = None, *, restart_realtime: bool = True
    ) -> None:
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
            self._seen_chunks.clear()
            realtime = self._realtime
            self._realtime = None
            if realtime is not None:
                await realtime.close()
            if (
                restart_realtime
                and self.enabled
                and self.provider_id == "soniox"
                and self.realtime_streaming
            ):
                try:
                    await self._start_realtime()
                except Exception as exc:
                    self.enabled = False
                    await self._send_json({
                        "type": "tts_error",
                        "epoch": self.epoch,
                        "provider_id": "soniox",
                        "message": f"Soniox real-time TTS reconnect failed: {exc}",
                        "recoverable": True,
                    })
        await self._send_state("on" if self.enabled else "off")

    async def close(self) -> None:
        if self._cleanup_complete:
            return
        self._cleanup_complete = True
        self.closed = True
        self.enabled = False
        realtime = self._realtime
        self._realtime = None
        if realtime is not None:
            await realtime.close()
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

    async def _send_realtime_audio(
        self, request: RealtimeRequest, audio: bytes, line_audio_end: bool
    ) -> None:
        if self.closed or not self.enabled or request.epoch != self.epoch:
            return
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
                    "streaming": True,
                })
                await self.browser_ws.send_bytes(audio)
            except (RuntimeError, WebSocketDisconnect):
                self.closed = True

    async def _send_realtime_complete(self, request: RealtimeRequest) -> None:
        if self.closed or request.epoch != self.epoch:
            return
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
            "provider_id": "soniox",
            "voice_id": request.voice,
            "characters": request.characters,
            "estimated_cost_usd": 0.0,
            "cache_hit": False,
        })

    async def _start_realtime(self) -> None:
        realtime = SonioxRealtimeSession(self, self.realtime_connector)
        try:
            await realtime.start(self._realtime_directions)
        except Exception:
            await realtime.close()
            raise
        self._realtime = realtime

    async def _realtime_failed(self, message: str) -> None:
        if self.closed or not self.enabled:
            return
        self.enabled = False
        realtime = self._realtime
        self._realtime = None
        if realtime is not None:
            await realtime.close()
        await self._send_json({
            "type": "tts_error",
            "epoch": self.epoch,
            "provider": "soniox",
            "provider_id": "soniox",
            "message": f"Soniox real-time TTS disconnected: {message}",
            "recoverable": True,
        })
        await self._send_state("error")

    def _directions_from_config(self, message: dict[str, Any]) -> dict[str, str]:
        mode = str(message.get("mode") or "one_way")
        if mode == "two_way":
            lang_a = str(message.get("lang_a") or "")
            lang_b = str(message.get("lang_b") or message.get("target_lang") or "")
            result: dict[str, str] = {}
            if lang_a:
                result[lang_a] = str(message.get("voice_b") or self.default_voice)
            if lang_b:
                result[lang_b] = self.default_voice
            return result
        target = str(message.get("target_lang") or "en")
        return {target: self.default_voice}

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
