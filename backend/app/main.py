"""FastAPI app: WebSocket proxy between the browser and the two Soniox
real-time APIs (STT+translation and TTS), plus a REST endpoint for fetching
saved transcripts.
"""

import asyncio
import base64
import json
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import websockets
from dotenv import load_dotenv
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import LANGUAGES, SONIOX_API_KEY, STT_URL, TTS_URL, VOICES, is_configured, set_api_key
from .context_builder import build_stt_config
from .stt import (
    TTS_END,
    TTS_NONE,
    handle_stt,
    pipe_browser_to_stt,
    stt_keepalive,
    stream_url_to_stt,
)
from .tts import (
    new_tts_state,
    pipe_tts_to_browser,
    prewarm_stream,
    trigger_barge,
    tts_keepalive,
    tts_sender,
)
from .transcript import TranscriptStore
from .config_store import save_config, load_config, is_configured as store_is_configured
from .config_store import (
    get_tts_api_key,
    set_tts_api_key,
    remove_tts_api_key,
    get_tts_provider,
    set_tts_provider,
    get_tts_voice,
    set_tts_voice,
)
from .logging_config import configure_logging, get_logger
from .tts_provider import get_provider, get_available_providers
from .external_tts import external_tts_sender
from .db import (
    init_db,
    close_db,
    create_conversation,
    update_conversation,
    add_connection_event,
    add_segments_batch,
    get_conversation,
    list_conversations,
    delete_conversation,
    search_conversations,
    export_conversation_txt,
    export_conversation_srt,
    export_conversation_json,
    cleanup_old_conversations,
    get_db_stats,
)

load_dotenv(override=True)
configure_logging()
log = get_logger("main")

transcript_store = TranscriptStore()

RECONNECT_MAX_RETRIES = 5
RECONNECT_BASE_DELAY_SECONDS = 0.5
RECONNECT_MAX_DELAY_SECONDS = 10.0
RECONNECT_JITTER_RATIO = 0.2
RECONNECT_EXHAUSTED_CLOSE_CODE = 4000
MAX_RECONNECT_AUDIO_BUFFER_BYTES = 500 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
async def client_config() -> JSONResponse:
    """Expose static option lists to the frontend."""
    return JSONResponse(
        {
            "voices": VOICES,
            "languages": [{"code": c, "name": n} for c, n in LANGUAGES],
            "configured": store_is_configured() or is_configured(),
        }
    )


@app.get("/setup/status")
async def setup_status() -> JSONResponse:
    """Tell the launcher/UI whether the API key is configured."""
    configured = store_is_configured() or is_configured()
    return JSONResponse({"configured": configured})


@app.get("/setup", include_in_schema=False)
async def setup_page() -> FileResponse:
    setup_html = os.path.join(_static_dir, "setup.html")
    return FileResponse(setup_html, media_type="text/html")


@app.post("/setup")
async def setup(payload: dict = Body(...)) -> JSONResponse:
    """First-run: accept {soniox_api_key: str, host?: str, port?: int} and
    persist to %APPDATA%/SonioxLiveTranslate/config.json. The running process
    picks up the new key immediately via `set_api_key`."""
    key = (payload.get("soniox_api_key") or "").strip()
    if not key:
        return JSONResponse(
            {"ok": False, "error": "soniox_api_key is required"},
            status_code=400,
        )
    cfg = load_config()
    cfg["soniox_api_key"] = key
    if payload.get("host"):
        cfg["host"] = payload["host"]
    if payload.get("port"):
        cfg["port"] = int(payload["port"])
    save_config(cfg)
    set_api_key(key)
    return JSONResponse({"ok": True, "configured": True})


@app.get("/transcript/{session_id}")
async def get_transcript(session_id: str) -> JSONResponse:
    session = transcript_store.get(session_id)
    return JSONResponse(session.payload())


@app.get("/api/conversations")
async def api_list_conversations(limit: int = 50, offset: int = 0) -> JSONResponse:
    convs = await list_conversations(limit=max(1, min(limit, 100)), offset=max(0, offset))
    return JSONResponse(convs)


@app.get("/api/conversations/search")
async def api_search_conversations(q: str = "", limit: int = 50, offset: int = 0) -> JSONResponse:
    if not q.strip():
        return JSONResponse([])
    convs = await search_conversations(
        q.strip(), limit=max(1, min(limit, 100)), offset=max(0, offset)
    )
    return JSONResponse(convs)


@app.get("/api/conversations/{conversation_id}")
async def api_get_conversation(conversation_id: str) -> JSONResponse:
    conv = await get_conversation(conversation_id)
    if conv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(conv)


@app.delete("/api/conversations/{conversation_id}")
async def api_delete_conversation(conversation_id: str) -> JSONResponse:
    await delete_conversation(conversation_id)
    return JSONResponse({"ok": True})


@app.get("/api/conversations/{conversation_id}/export", response_model=None)
async def api_export_conversation(
    conversation_id: str, format: str = "json"
) -> Response:
    if await get_conversation(conversation_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    headers = {"Content-Disposition": f'attachment; filename="conversation-{conversation_id}.{format}"'}
    if format == "json":
        content = await export_conversation_json(conversation_id)
        return Response(content, media_type="application/json", headers=headers)
    elif format == "txt":
        content = await export_conversation_txt(conversation_id)
        return Response(content, media_type="text/plain; charset=utf-8", headers=headers)
    elif format == "srt":
        content = await export_conversation_srt(conversation_id)
        return Response(content, media_type="application/x-subrip; charset=utf-8", headers=headers)
    else:
        return JSONResponse({"error": f"Unsupported format: {format}"}, status_code=400)


@app.post("/api/retention/cleanup")
async def api_cleanup(max_age_days: int = 30) -> JSONResponse:
    deleted = await cleanup_old_conversations(max_age_days=max(1, min(max_age_days, 3650)))
    return JSONResponse({"deleted": deleted})


@app.get("/api/retention/stats")
async def api_retention_stats() -> JSONResponse:
    stats = await get_db_stats()
    return JSONResponse(stats)


@app.get("/api/tts/providers")
async def api_tts_providers() -> JSONResponse:
    providers = get_available_providers()
    result = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "requires_api_key": p.requires_api_key,
            "supports_streaming": p.supports_streaming,
            "pricing_url": p.pricing_url,
            "approximate_cost_per_1m_chars": p.approximate_cost_per_1m_chars,
            "has_api_key": bool(get_tts_api_key(p.id)),
        }
        for p in providers
    ]
    return JSONResponse(result)


@app.get("/api/tts/providers/{provider_id}/voices")
async def api_tts_provider_voices(provider_id: str, lang: str = "en") -> JSONResponse:
    api_key = get_tts_api_key(provider_id)
    provider = get_provider(provider_id, api_key=api_key)
    if provider is None:
        return JSONResponse({"error": f"Unknown provider: {provider_id}"}, status_code=404)
    voices = await provider.list_voices(lang=lang)
    return JSONResponse([
        {"id": v.id, "name": v.name, "language": v.language, "gender": v.gender}
        for v in voices
    ])


@app.post("/api/tts/config")
async def api_tts_config(payload: dict = Body(...)) -> JSONResponse:
    provider_id = payload.get("provider_id")
    api_key = payload.get("api_key", "").strip()
    if provider_id and api_key:
        set_tts_api_key(provider_id, api_key)
    if provider_id:
        set_tts_provider(provider_id)
    if "voice" in payload and provider_id:
        set_tts_voice(provider_id, payload["voice"])
    return JSONResponse({"ok": True})


@app.get("/api/tts/config")
async def api_get_tts_config() -> JSONResponse:
    providers = get_available_providers()
    provider_keys = {}
    for p in providers:
        key = get_tts_api_key(p.id)
        if key:
            provider_keys[p.id] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
    return JSONResponse({
        "current_provider": get_tts_provider(),
        "current_voice": get_tts_voice(get_tts_provider()),
        "configured_providers": provider_keys,
    })


@app.websocket("/ws/translate")
async def translation_websocket(
    browser_ws: WebSocket,
    mode: str = "one_way",
    target_lang: str = "en",
    lang_a: str | None = None,
    lang_b: str | None = None,
    lang_id: bool = True,
    diarize: bool = True,
    voice: str = "Maya",
    voice_b: str | None = None,
    tts: bool = True,
    context_b64: str | None = None,
    audio_url: str | None = None,
    audio_duration: float | None = None,
    input_device: str | None = None,
    output_device: str | None = None,
    tts_provider: str = "soniox",
) -> None:
    await browser_ws.accept()

    if not is_configured():
        await browser_ws.send_json(
            {
                "error_code": "not_configured",
                "error_message": "Soniox API key not set. Open /setup in the browser to configure.",
            }
        )
        await browser_ws.close()
        return

    context = _parse_context(context_b64)
    stt_config = build_stt_config(
        mode=mode,
        target_lang=target_lang,
        lang_a=lang_a,
        lang_b=lang_b,
        lang_id=lang_id,
        diarize=diarize,
        context=context,
    )

    if mode == "two_way":
        if not lang_a or not lang_b:
            await browser_ws.send_json(
                {"error_code": "bad_config", "error_message": "two_way requires lang_a and lang_b"}
            )
            await browser_ws.close()
            return
        directions = [lang_a, lang_b]
        direction_voices = {
            lang_a: voice_b or voice,
            lang_b: voice,
        }
    else:
        directions = [target_lang]
        direction_voices = {target_lang: voice}

    session = transcript_store.new()
    await browser_ws.send_json({"session_id": session.id})

    # Create DB conversation record on first connection
    conv_id = session.id
    conv_started = int(time.time() * 1000)
    await create_conversation(
        id=conv_id,
        started_at=conv_started,
        mode=mode,
        target_lang=target_lang,
        source_lang=lang_a if mode == "two_way" else None,
        input_device=input_device,
        output_device=output_device,
        tts_provider=tts_provider,
        tts_voice=voice,
    )
    pending_segments: list[dict] = []
    segment_batch_size = 10

    async def on_final_segment(seg: dict) -> None:
        pending_segments.append({"conversation_id": conv_id, "is_final": True, **seg})
        if len(pending_segments) >= segment_batch_size:
            batch = pending_segments[:]
            pending_segments.clear()
            await add_segments_batch(batch)

    tts_queue: asyncio.Queue | None = asyncio.Queue() if tts else None
    tts_state: dict = new_tts_state(directions)
    use_external_tts = tts and tts_provider != "soniox"
    external_tts_task: asyncio.Task | None = None
    if use_external_tts and tts_queue is not None:
        external_provider = get_provider(
            tts_provider,
            api_key=get_tts_api_key(tts_provider),
        )
        external_tts_task = asyncio.create_task(
            external_tts_sender(
                tts_queue=tts_queue,
                tts_state=tts_state,
                browser_ws=browser_ws,
                provider_id=tts_provider,
                provider=external_provider,
                direction_voices=direction_voices,
            )
        )

    # Audio buffer for pending data during reconnection (mic mode only).
    audio_buffer: bytearray = bytearray()
    audio_dropped_bytes = 0

    # Pre-compute input coroutine factory for mic mode.
    _on_text: dict = {}

    async def on_text(data: dict) -> None:
        if data.get("type") == "utterances":
            session.add_many(data.get("utterances", []))
        elif data.get("type") == "barge":
            if tts_queue is not None:
                await trigger_barge(tts_queue, tts_state)
                try:
                    await browser_ws.send_json({"barge_ack": True})
                except Exception:
                    pass

    _on_text["fn"] = on_text

    async def do_browser_to_stt(stt_ws):
        if audio_url and audio_duration:
            await stream_url_to_stt(
                audio_url=audio_url,
                duration=audio_duration,
                browser_ws=browser_ws,
                stt_ws=stt_ws,
            )
            return
        await pipe_browser_to_stt(
            browser_ws=browser_ws, stt_ws=stt_ws, on_text=_on_text["fn"]
        )

    retry_count = 0
    downtime_start = 0.0
    first_connection = True
    browser_disconnected = False

    while True:
        stt_ws = None
        tts_ws = None
        # retry_count also covers failures before the first STT connection
        # succeeds. Without it, the UI remains stuck in "reconnecting" after
        # an initial connect failure eventually recovers.
        is_reconnect = retry_count > 0 or not first_connection
        disconnect_code: int | None = None
        disconnect_reason: str | None = None
        stt_finished_event = asyncio.Event()

        try:
            # Connect to Soniox STT
            stt_ws = await websockets.connect(
                STT_URL, ping_interval=10, ping_timeout=10, close_timeout=5
            )
            await stt_ws.send(json.dumps(stt_config))

            if is_reconnect:
                # Report reconnection success
                downtime_ms = int((time.monotonic() - downtime_start) * 1000)
                reconnect_payload = {
                    "reconnected": True,
                    "downtime_ms": downtime_ms,
                    "buffered_audio_bytes": len(audio_buffer),
                    "dropped_audio_bytes": audio_dropped_bytes,
                }
                if audio_dropped_bytes:
                    reconnect_payload["downtime_text"] = (
                        f"[mất âm thanh do gián đoạn ~{downtime_ms / 1000:.1f}s; buffer đầy]"
                    )
                await _safe_send_json(browser_ws, reconnect_payload)
                log.info(
                    "stt_reconnected",
                    retry_count=retry_count,
                    downtime_ms=downtime_ms,
                    buffered_audio_bytes=len(audio_buffer),
                    dropped_audio_bytes=audio_dropped_bytes,
                )
                await add_connection_event(
                    conversation_id=conv_id,
                    soniox_session_id=session.id,
                    event_type="reconnect",
                    occurred_at=int(time.time() * 1000),
                )
                # Flush buffered audio
                if audio_buffer:
                    await stt_ws.send(bytes(audio_buffer))
                    log.info(
                        "audio_buffer_flushed",
                        bytes=len(audio_buffer),
                        dropped_audio_bytes=audio_dropped_bytes,
                        downtime_ms=downtime_ms,
                    )
                    audio_buffer.clear()
                retry_count = 0
                downtime_start = 0.0
                audio_dropped_bytes = 0
            else:
                await add_connection_event(
                    conversation_id=conv_id,
                    soniox_session_id=session.id,
                    event_type="connect",
                    occurred_at=int(time.time() * 1000),
                )

            if is_reconnect and tts and not use_external_tts:
                # Streams belong to the previous TTS WebSocket and cannot be
                # reused after reconnecting.
                tts_state = new_tts_state(directions)

            if tts and not use_external_tts and tts_ws is None:
                tts_ws = await websockets.connect(
                    TTS_URL, ping_interval=10, ping_timeout=10, close_timeout=5
                )
                for direction in directions:
                    await prewarm_stream(
                        tts_ws=tts_ws,
                        tts_state=tts_state,
                        direction=direction,
                        voice=direction_voices[direction],
                    )

            first_connection = False

            async with asyncio.TaskGroup() as tg:
                tg.create_task(do_browser_to_stt(stt_ws))
                tg.create_task(
                    handle_stt(
                        stt_ws=stt_ws,
                        browser_ws=browser_ws,
                        tts_queue=tts_queue,
                        tts_state=tts_state,
                        mode=mode,
                        lang_a=lang_a,
                        lang_b=lang_b,
                        target_lang=target_lang,
                        on_final_segment=on_final_segment,
                        finalize_session_on_exit=False,
                        finished_event=stt_finished_event,
                    )
                )
                tg.create_task(stt_keepalive(stt_ws))
                if tts and not use_external_tts:
                    tg.create_task(
                        tts_sender(
                            tts_queue=tts_queue,
                            tts_state=tts_state,
                            tts_ws=tts_ws,
                            direction_voices=direction_voices,
                            browser_ws=browser_ws,
                        )
                    )
                    tg.create_task(
                        pipe_tts_to_browser(
                            tts_ws=tts_ws,
                            browser_ws=browser_ws,
                            tts_state=tts_state,
                        )
                    )
                    tg.create_task(tts_keepalive(tts_ws=tts_ws))

        except* WebSocketDisconnect:
            log.debug("browser_ws_disconnect")
            browser_disconnected = True
        except* RuntimeError as eg:
            log.debug("ws_runtime_error", errors=[str(e) for e in eg.exceptions])
            if _has_browser_disconnect(eg):
                browser_disconnected = True
            else:
                disconnect_code, disconnect_reason = _connection_close_details(eg)
        except* Exception as eg:
            if _has_browser_disconnect(eg):
                browser_disconnected = True
            else:
                disconnect_code, disconnect_reason = _connection_close_details(eg)
                log.error(
                    "ws_session_error",
                    errors=[str(e) for e in eg.exceptions],
                    close_code=disconnect_code,
                    close_reason=disconnect_reason,
                )
        finally:
            if not browser_disconnected and disconnect_code is None:
                disconnect_code, disconnect_reason = _websocket_close_details(stt_ws)
            if stt_ws is not None:
                try:
                    await stt_ws.close()
                except Exception:
                    pass
            if tts_ws is not None:
                try:
                    await tts_ws.close()
                except Exception:
                    pass

        if browser_disconnected:
            break
        if stt_finished_event.is_set():
            break

        if downtime_start == 0:
            downtime_start = time.monotonic()
        disconnected_at_ms = int(time.time() * 1000)
        downtime_ms = int((time.monotonic() - downtime_start) * 1000)
        log.warning(
            "stt_disconnected",
            disconnected_at_ms=disconnected_at_ms,
            close_code=disconnect_code,
            close_reason=disconnect_reason,
            retry_count=retry_count,
            downtime_ms=downtime_ms,
        )
        await add_connection_event(
            conversation_id=conv_id,
            soniox_session_id=session.id,
            event_type="disconnect",
            close_code=disconnect_code,
            close_reason=(disconnect_reason or "")[:200],
            occurred_at=disconnected_at_ms,
        )

        # If we reach here without a browser disconnect, try reconnecting
        if retry_count < RECONNECT_MAX_RETRIES:
            retry_count += 1
            delay = _reconnect_delay(retry_count)

            await _safe_send_json(browser_ws, {
                "reconnecting": True,
                "attempt": retry_count,
                "max_attempts": RECONNECT_MAX_RETRIES,
                "downtime_start": int(downtime_start * 1000),
            })
            log.info(
                "stt_reconnecting",
                attempt=retry_count,
                max_attempts=RECONNECT_MAX_RETRIES,
                delay_seconds=round(delay, 3),
                downtime_ms=downtime_ms,
                close_code=disconnect_code,
                close_reason=disconnect_reason,
            )

            # Buffer any incoming audio during the wait period
            try:
                dropped_bytes = await _buffer_audio_during_reconnect(
                    browser_ws,
                    audio_buffer,
                    MAX_RECONNECT_AUDIO_BUFFER_BYTES,
                    delay,
                    on_text=_on_text["fn"],
                )
                audio_dropped_bytes += dropped_bytes
                if dropped_bytes:
                    log.warning(
                        "reconnect_audio_buffer_overflow",
                        dropped_bytes=dropped_bytes,
                        total_dropped_bytes=audio_dropped_bytes,
                        buffered_bytes=len(audio_buffer),
                        max_buffer_bytes=MAX_RECONNECT_AUDIO_BUFFER_BYTES,
                        retry_count=retry_count,
                        downtime_ms=int((time.monotonic() - downtime_start) * 1000),
                    )
            except WebSocketDisconnect:
                browser_disconnected = True
                break
        else:
            downtime_ms = int((time.monotonic() - downtime_start) * 1000)
            await _safe_send_json(browser_ws, {
                "reconnect_failed": True,
                "downtime_ms": downtime_ms,
                "max_retries": RECONNECT_MAX_RETRIES,
                "error_message": "Không thể kết nối lại sau nhiều lần thử. Vui lòng kiểm tra kết nối mạng.",
            })
            log.error(
                "stt_reconnect_failed",
                retries=RECONNECT_MAX_RETRIES,
                downtime_ms=downtime_ms,
                close_code=disconnect_code,
                close_reason=disconnect_reason,
                dropped_audio_bytes=audio_dropped_bytes,
            )
            try:
                await browser_ws.close(
                    code=RECONNECT_EXHAUSTED_CLOSE_CODE,
                    reason="stt_reconnect_exhausted",
                )
            except Exception:
                pass
            break

    # Session cleanup
    if stt_ws is not None:
        try:
            await stt_ws.close()
        except Exception:
            pass
    if tts_ws is not None:
        try:
            await tts_ws.close()
        except Exception:
            pass
    if tts_queue is not None:
        await tts_queue.put((TTS_END, None))
        await tts_queue.put(TTS_NONE)
    if external_tts_task is not None:
        try:
            await asyncio.wait_for(external_tts_task, timeout=35.0)
        except asyncio.TimeoutError:
            external_tts_task.cancel()
        except Exception as exc:
            log.warning("external_tts_task_failed", error=str(exc))
    tts_state["stt_done"] = True
    session.close()
    transcript_store.finish(session)
    if pending_segments:
        await add_segments_batch(pending_segments)
        pending_segments.clear()
    await update_conversation(conv_id, ended_at=int(time.time() * 1000))


async def _safe_send_json(ws: WebSocket, data: dict) -> None:
    try:
        await ws.send_json(data)
    except Exception:
        pass


async def _buffer_audio_during_reconnect(
    browser_ws: WebSocket,
    audio_buffer: bytearray,
    max_bytes: int,
    max_wait: float,
    on_text: Callable[[dict], Awaitable[None]] | None = None,
) -> int:
    """Drain browser messages while reconnecting and return dropped bytes.

    The buffer always retains the newest ``max_bytes`` of audio. Text control
    messages (transcript snapshots and barge-in) are still dispatched instead
    of being silently discarded during the outage.
    """
    dropped_bytes = 0
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            msg = await asyncio.wait_for(browser_ws.receive(), timeout=min(remaining, 0.5))
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(
                    code=msg.get("code", 1000),
                    reason=msg.get("reason", ""),
                )
            if "bytes" in msg and msg["bytes"] is not None:
                data = msg["bytes"]
                if max_bytes <= 0:
                    dropped_bytes += len(data)
                elif len(data) >= max_bytes:
                    dropped_bytes += len(audio_buffer) + len(data) - max_bytes
                    audio_buffer[:] = data[-max_bytes:]
                elif len(audio_buffer) + len(data) <= max_bytes:
                    audio_buffer.extend(data)
                else:
                    overflow = len(audio_buffer) + len(data) - max_bytes
                    del audio_buffer[:overflow]
                    audio_buffer.extend(data)
                    dropped_bytes += overflow
            elif msg.get("text") is not None and on_text is not None:
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                await on_text(data)
        except asyncio.TimeoutError:
            continue
        except WebSocketDisconnect:
            raise
        except Exception as e:
            log.warning("reconnect_audio_buffer_stopped", error=str(e))
            break
    return dropped_bytes


def _reconnect_delay(attempt: int, random_value: float | None = None) -> float:
    """Return capped exponential backoff with up to 20% positive jitter."""
    normalized_attempt = max(1, attempt)
    exponential = min(
        RECONNECT_BASE_DELAY_SECONDS * (2 ** (normalized_attempt - 1)),
        RECONNECT_MAX_DELAY_SECONDS,
    )
    sample = random.random() if random_value is None else min(max(random_value, 0.0), 1.0)
    return min(
        exponential * (1 + RECONNECT_JITTER_RATIO * sample),
        RECONNECT_MAX_DELAY_SECONDS,
    )


def _iter_leaf_exceptions(error: BaseException) -> Iterator[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            yield from _iter_leaf_exceptions(nested)
        return
    yield error


def _close_frame_details(source) -> tuple[int | None, str | None]:
    code = getattr(source, "code", None)
    reason = getattr(source, "reason", None)
    if code is None:
        for frame_name in ("rcvd", "sent"):
            frame = getattr(source, frame_name, None)
            if frame is not None:
                code = getattr(frame, "code", None)
                reason = getattr(frame, "reason", None)
                if code is not None:
                    break
    try:
        normalized_code = int(code) if code is not None else None
    except (TypeError, ValueError):
        normalized_code = None
    return normalized_code, str(reason) if reason else None


def _connection_close_details(error: BaseException) -> tuple[int, str]:
    """Extract the actual WebSocket close code/reason from an exception tree."""
    fallback_reason = ""
    for leaf in _iter_leaf_exceptions(error):
        code, reason = _close_frame_details(leaf)
        if not fallback_reason and str(leaf):
            fallback_reason = str(leaf)
        if code is not None:
            return code, reason or fallback_reason or type(leaf).__name__
    return 1006, fallback_reason or type(error).__name__


def _websocket_close_details(stt_ws) -> tuple[int, str]:
    if stt_ws is not None:
        code = getattr(stt_ws, "close_code", None)
        reason = getattr(stt_ws, "close_reason", None)
        if code is None:
            code, reason = _close_frame_details(stt_ws)
        else:
            try:
                code = int(code)
            except (TypeError, ValueError):
                code = None
        if code is not None:
            return code, str(reason) if reason else "STT WebSocket closed"
    return 1006, "STT WebSocket closed without a close frame"


def _has_browser_disconnect(eg: BaseExceptionGroup) -> bool:
    """Check if any exception in the group is a browser WebSocket disconnect."""
    for e in _iter_leaf_exceptions(eg):
        if isinstance(e, WebSocketDisconnect):
            return True
        if isinstance(e, RuntimeError) and "close message has been sent" in str(e):
            return True
    return False


def _parse_context(context_b64: str | None) -> dict | None:
    if not context_b64:
        return None
    try:
        raw = base64.urlsafe_b64decode(context_b64.encode("ascii")).decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        log.warning("context_parse_failed", error=str(e))
        return None


# Serve the frontend as static files at /.
# When packaged by PyInstaller, `__file__` points into the app's PYZ and is
# not a real filesystem path relative to the bundle root — the frontend
# assets live at `<_MEIPASS>/frontend/dist` (see installer/spec.spec `datas`),
# so that path must be resolved from `sys._MEIPASS` directly instead of via
# `__file__` when frozen.
if getattr(sys, "frozen", False):
    _static_dir = str(Path(sys._MEIPASS) / "frontend" / "dist")  # type: ignore[attr-defined]
else:
    # Prefer the Vite build output (frontend/dist/) if it exists; fall back
    # to the source frontend/ for dev and the no-build-step vanilla fallback.
    _frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    _dist_dir = os.path.join(_frontend_dir, "dist")
    _static_dir = _dist_dir if os.path.isdir(_dist_dir) else _frontend_dir
app.mount(
    "/",
    StaticFiles(directory=_static_dir, html=True),
    name="static",
)
