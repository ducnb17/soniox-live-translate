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
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import websockets
from dotenv import load_dotenv
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import (
    LANGUAGES,
    MAX_ENDPOINT_DELAY_MS,
    MIN_ENDPOINT_DELAY_MS,
    STT_URL,
    TTS_URL,
    VOICES,
    is_configured,
    set_api_key,
)
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
    get_api_key,
    get_stt_api_key,
    set_stt_api_key,
    get_stt_provider,
    set_stt_provider,
    get_translation_api_key,
    set_translation_api_key,
    get_translation_provider,
    set_translation_provider,
    get_tts_api_key,
    set_tts_api_key,
    remove_tts_api_key,
    get_tts_provider,
    set_tts_provider,
    get_tts_voice,
    set_tts_voice,
)
from .logging_config import configure_logging, get_logger
from .tts_provider import (
    get_provider as get_tts_provider_instance,
    get_available_providers as get_available_tts_providers,
)
from .stt_provider import (
    get_provider as get_stt_provider_instance,
    get_available_providers as get_available_stt_providers,
)
from .stt_providers.google_provider import GoogleSttStream
from .translation_provider import (
    get_provider as get_translation_provider_instance,
    get_available_providers as get_available_translation_providers,
)
from .external_tts import external_tts_sender
from .version import APP_VERSION
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


def _mask_key(key: str) -> str:
    """Return a masked version of an API key for safe display in UI."""
    if not key:
        return ""
    if len(key) > 8:
        return key[:4] + "****" + key[-4:]
    return "****"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/version")
async def api_version() -> dict[str, str]:
    return {"version": APP_VERSION}


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
    providers = get_available_tts_providers()
    result = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "requires_api_key": p.requires_api_key,
            "supports_streaming": p.supports_streaming,
            "tier": p.tier,
            "pricing_url": p.pricing_url,
            "approximate_cost_per_1m_chars": p.approximate_cost_per_1m_chars,
            "has_api_key": bool(get_tts_api_key(p.id)) or (p.id == "soniox" and bool(get_api_key())),
        }
        for p in providers
    ]
    return JSONResponse(result)


@app.get("/api/tts/providers/{provider_id}/voices")
async def api_tts_provider_voices(provider_id: str, lang: str = "en") -> JSONResponse:
    api_key = get_tts_api_key(provider_id)
    provider = get_tts_provider_instance(provider_id, api_key=api_key)
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
    """Return TTS and STT config so the frontend knows the current provider
    and whether a key is already stored for each."""
    providers = get_available_tts_providers()
    tts_provider_keys = {}
    for p in providers:
        key = get_tts_api_key(p.id)
        if key:
            tts_provider_keys[p.id] = _mask_key(key)

    current_stt = get_stt_provider()
    stt_key = get_stt_api_key(current_stt)
    stt_api_key_present = bool(stt_key)

    current_translation = get_translation_provider()
    translation_key = get_translation_api_key(current_translation)
    translation_api_key_present = bool(translation_key)

    return JSONResponse({
        "current_provider": get_tts_provider(),
        "current_voice": get_tts_voice(get_tts_provider()),
        "configured_providers": tts_provider_keys,
        "selected_stt_provider": current_stt,
        "stt_api_key_present": stt_api_key_present,
        "stt_api_key_masked": _mask_key(stt_key) if stt_key else "",
        "selected_translation_provider": current_translation,
        "translation_api_key_present": translation_api_key_present,
        "translation_api_key_masked": _mask_key(translation_key) if translation_key else "",
    })


@app.post("/api/config/save")
async def api_save_config(payload: dict = Body(...)) -> JSONResponse:
    """Unified save endpoint: writes provider selections and API keys in one call.

    Any key field that is empty/missing keeps the existing stored value.
    """
    tts_provider = str(payload.get("tts_provider") or "")
    tts_voice = str(payload.get("tts_voice") or "")
    stt_provider = str(payload.get("stt_provider") or "")
    translation_provider = str(payload.get("translation_provider") or "")

    tts_api_key = str(payload.get("tts_api_key") or "").strip()
    stt_api_key = str(payload.get("stt_api_key") or "").strip()
    translation_api_key = str(payload.get("translation_api_key") or "").strip()

    # Save TTS
    if tts_provider:
        set_tts_provider(tts_provider)
        if tts_api_key:
            set_tts_api_key(tts_provider, tts_api_key)
        if tts_voice:
            set_tts_voice(tts_provider, tts_voice)

    # Save STT
    if stt_provider:
        set_stt_provider(stt_provider)
        if stt_api_key:
            set_stt_api_key(stt_provider, stt_api_key)

    # Save Translation
    if translation_provider:
        set_translation_provider(translation_provider)
        if translation_api_key:
            set_translation_api_key(translation_provider, translation_api_key)

    return JSONResponse({"ok": True})


@app.post("/api/tts/providers/{provider_id}/test")
async def api_test_tts_provider(provider_id: str, payload: dict = Body(...)) -> JSONResponse:
    key = str(payload.get("api_key") or "").strip()
    provider = get_tts_provider_instance(provider_id, api_key=key or None)
    if provider is None:
        return JSONResponse({"ok": False, "message": f"Unknown provider: {provider_id}"}, status_code=404)
    ok, message = await provider.test_connection()
    if ok:
        if key:
            set_tts_api_key(provider_id, key)
            if provider_id == "soniox":
                cfg = load_config()
                cfg["soniox_api_key"] = key
                save_config(cfg)
                set_api_key(key)
        set_tts_provider(provider_id)
    return JSONResponse({"ok": ok, "message": message})


@app.get("/api/stt/providers")
async def api_stt_providers() -> JSONResponse:
    return JSONResponse([
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "requires_api_key": p.requires_api_key,
            "supports_streaming": p.supports_streaming,
            "supports_realtime_translation": p.supports_realtime_translation,
            "tier": p.tier,
            "pricing_url": p.pricing_url,
            "approximate_cost_per_hour": p.approximate_cost_per_hour,
            "has_api_key": bool(get_stt_api_key(p.id)) or (p.id == "soniox" and bool(get_api_key())),
        }
        for p in get_available_stt_providers()
    ])


@app.post("/api/stt/providers/{provider_id}/test")
async def api_test_stt_provider(provider_id: str, payload: dict = Body(...)) -> JSONResponse:
    key = str(payload.get("api_key") or "").strip()
    provider = get_stt_provider_instance(provider_id, api_key=key or None)
    if provider is None:
        return JSONResponse({"ok": False, "message": f"Unknown provider: {provider_id}"}, status_code=404)
    ok, message = await provider.test_connection()
    if ok:
        if key:
            set_stt_api_key(provider_id, key)
            if provider_id == "soniox":
                cfg = load_config()
                cfg["soniox_api_key"] = key
                save_config(cfg)
                set_api_key(key)
        set_stt_provider(provider_id)
    return JSONResponse({"ok": ok, "message": message})


@app.get("/api/stt/config")
async def api_get_stt_config() -> JSONResponse:
    return JSONResponse({"current_provider": get_stt_provider()})


@app.post("/api/stt/config")
async def api_set_stt_config(payload: dict = Body(...)) -> JSONResponse:
    provider_id = str(payload.get("provider_id") or "")
    if get_stt_provider_instance(provider_id) is None:
        return JSONResponse({"ok": False, "message": f"Unknown provider: {provider_id}"}, status_code=404)
    set_stt_provider(provider_id)
    return JSONResponse({"ok": True})


@app.get("/api/translation/providers")
async def api_translation_providers() -> JSONResponse:
    return JSONResponse([
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "requires_api_key": p.requires_api_key,
            "supports_realtime_translation": p.supports_realtime_translation,
            "tier": p.tier,
            "pricing_url": p.pricing_url,
            "signup_url": p.signup_url,
            "has_api_key": bool(get_translation_api_key(p.id)) or (p.id == "soniox" and bool(get_api_key())),
        }
        for p in get_available_translation_providers()
    ])


@app.post("/api/translation/providers/{provider_id}/test")
async def api_test_translation_provider(provider_id: str, payload: dict = Body(...)) -> JSONResponse:
    key = str(payload.get("api_key") or "").strip()
    provider = get_translation_provider_instance(provider_id, api_key=key or None)
    if provider is None:
        return JSONResponse({"ok": False, "message": f"Unknown provider: {provider_id}"}, status_code=404)
    ok, message = await provider.test_connection()
    if ok:
        if key:
            set_translation_api_key(provider_id, key)
            if provider_id == "soniox":
                cfg = load_config()
                cfg["soniox_api_key"] = key
                save_config(cfg)
                set_api_key(key)
        set_translation_provider(provider_id)
    return JSONResponse({"ok": ok, "message": message})


@app.get("/api/translation/config")
async def api_get_translation_config() -> JSONResponse:
    return JSONResponse({"current_provider": get_translation_provider()})


@app.post("/api/translation/config")
async def api_set_translation_config(payload: dict = Body(...)) -> JSONResponse:
    provider_id = str(payload.get("provider_id") or "")
    if get_translation_provider_instance(provider_id) is None:
        return JSONResponse({"ok": False, "message": f"Unknown provider: {provider_id}"}, status_code=404)
    set_translation_provider(provider_id)
    return JSONResponse({"ok": True})


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
    tts_enabled: bool | None = None,
    context_b64: str | None = None,
    audio_url: str | None = None,
    audio_duration: float | None = None,
    input_device: str | None = None,
    output_device: str | None = None,
    tts_provider: str = "soniox",
    stt_provider: str = "soniox",
    translation_provider: str = "soniox",
    stt_delay_ms: int = MAX_ENDPOINT_DELAY_MS,
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

    if stt_provider not in ("soniox", "google_v2"):
        provider = get_stt_provider_instance(
            stt_provider, api_key=get_stt_api_key(stt_provider)
        )
        message = (
            f"{provider.info.name if provider else stt_provider} is configured, "
            "but its live audio adapter is not available for this browser stream"
        )
        await browser_ws.send_json({"error_code": "stt_adapter_unavailable", "error_message": message})
        await browser_ws.close()
        return

    # Validate Google V2 credentials early
    if stt_provider == "google_v2":
        google_provider = get_stt_provider_instance(
            "google_v2", api_key=get_stt_api_key("google_v2")
        )
        if google_provider is None:
            await browser_ws.send_json({
                "error_code": "bad_stt_provider",
                "error_message": "Google STT V2 provider could not be initialised",
            })
            await browser_ws.close()
            return
        google_ok, google_msg = await google_provider.test_connection()
        if not google_ok:
            await browser_ws.send_json({
                "error_code": "google_v2_credentials",
                "error_message": f"Google Cloud credentials check failed: {google_msg}",
            })
            await browser_ws.close()
            return

    translation_engine = get_translation_provider_instance(
        translation_provider,
        api_key=(
            None
            if translation_provider == "soniox"
            else get_translation_api_key(translation_provider)
        ),
    )
    if translation_engine is None:
        await browser_ws.send_json({
            "error_code": "bad_translation_provider",
            "error_message": f"Unknown translation provider: {translation_provider}",
        })
        await browser_ws.close()
        return

    translate_text = None if translation_provider == "soniox" else translation_engine.translate

    context = _parse_context(context_b64)
    # Honor the frontend's endpointing choice within a safe window. Earlier
    # versions floored at MAX_ENDPOINT_DELAY_MS (3000 ms), which forced every
    # session into a slow <end> and inflated end-to-end latency. Now we cap
    # at 3000 ms (upper bound to avoid cutting off long pauses) and floor at
    # MIN_ENDPOINT_DELAY_MS (lower bound so we don't get spurious endpoints).
    endpoint_delay_ms = max(MIN_ENDPOINT_DELAY_MS, min(3000, stt_delay_ms if stt_delay_ms else MAX_ENDPOINT_DELAY_MS))
    extra_hold_ms = max(0, stt_delay_ms - 3000)
    language_hints: list[str] = []
    if mode == "two_way":
        if lang_a:
            language_hints.append(lang_a)
        if lang_b:
            language_hints.append(lang_b)
    stt_config = build_stt_config(
        mode=mode,
        target_lang=target_lang,
        lang_a=lang_a,
        lang_b=lang_b,
        lang_id=lang_id,
        diarize=diarize,
        context=context,
        max_endpoint_delay_ms=endpoint_delay_ms,
        enable_translation=translation_provider == "soniox",
        language_hints=language_hints or None,
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

    # Transport availability and subscriber state are intentionally distinct.
    # Existing clients can still opt out of the transport with `tts=false`;
    # the frontend keeps it available and toggles `tts_enabled` dynamically.
    tts_queue: asyncio.Queue | None = asyncio.Queue() if tts else None
    tts_state: dict = new_tts_state(directions)
    tts_state["enabled"] = tts and (tts if tts_enabled is None else tts_enabled)
    use_external_tts = tts and tts_provider != "soniox"
    external_tts_task: asyncio.Task | None = None
    if use_external_tts and tts_queue is not None:
        external_provider = get_tts_provider_instance(
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
        elif data.get("type") == "tts_control":
            enabled = bool(data.get("enabled"))
            was_enabled = bool(tts_state.get("enabled"))
            tts_state["enabled"] = enabled and tts_queue is not None
            if was_enabled and not enabled and tts_queue is not None:
                # Cancel queued/in-flight synthesis, but leave STT untouched.
                await trigger_barge(tts_queue, tts_state)
            try:
                await browser_ws.send_json({
                    "type": "tts_control_ack",
                    "enabled": tts_state["enabled"],
                })
            except Exception:
                pass

    _on_text["fn"] = on_text

    async def do_browser_to_stt(stt_ws):
        if audio_url and audio_duration:
            control_task = asyncio.create_task(
                pipe_browser_to_stt(
                    browser_ws=browser_ws,
                    stt_ws=stt_ws,
                    on_text=_on_text["fn"],
                )
            )
            try:
                await stream_url_to_stt(
                    audio_url=audio_url,
                    duration=audio_duration,
                    browser_ws=browser_ws,
                    stt_ws=stt_ws,
                )
            finally:
                control_task.cancel()
                with suppress(asyncio.CancelledError):
                    await control_task
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
            # Connect to Soniox STT (or Google V2 adapter)
            use_google_v2 = stt_provider == "google_v2"
            if use_google_v2:
                stt_ws = GoogleSttStream(google_provider, language_hints)
                await stt_ws.open()
            else:
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
                previous_line_counter = tts_state.get("line_counter", 0)
                previous_tts_enabled = bool(tts_state.get("enabled"))
                tts_state = new_tts_state(directions)
                tts_state["line_counter"] = previous_line_counter
                tts_state["enabled"] = previous_tts_enabled

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
                        extra_hold_ms=extra_hold_ms,
                        translate_text=translate_text,
                        stream_translation_tokens=(
                            tts and not use_external_tts and translate_text is None
                        ),
                    )
                )
                tg.create_task(stt_keepalive(stt_ws)) if not use_google_v2 else None
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
                            direction_voices=direction_voices,
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
    r = random_value if random_value is not None else random.random()
    jitter = exponential * RECONNECT_JITTER_RATIO
    return min(exponential + r * jitter, RECONNECT_MAX_DELAY_SECONDS)


def _parse_context(context_b64: str | None) -> dict | None:
    if not context_b64:
        return None
    try:
        return json.loads(base64.b64decode(context_b64).decode("utf-8"))
    except Exception:
        return {}


def _has_browser_disconnect(eg: ExceptionGroup | BaseExceptionGroup) -> bool:
    for exc in eg.exceptions:
        if isinstance(exc, WebSocketDisconnect):
            return True
        if isinstance(exc, BaseExceptionGroup):
            if _has_browser_disconnect(exc):
                return True
    return False


def _connection_close_details(eg: ExceptionGroup | BaseExceptionGroup) -> tuple[int | None, str | None]:
    for exc in eg.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            code, reason = _connection_close_details(exc)
            if code is not None:
                return code, reason
        # websockets 16 no longer exposes ``websockets.exceptions`` as a
        # package attribute. The public close shape is stable, and checking
        # it directly also preserves details from wrapped transport errors.
        code = getattr(exc, "code", None)
        reason = getattr(exc, "reason", None)
        if code is not None:
            return code, reason
        if isinstance(exc, ConnectionError):
            return 1006, str(exc)
    return None, None


def _websocket_close_details(ws) -> tuple[int | None, str | None]:
    if ws is None:
        return 1006, "websocket was None (unexpected connection state)"
    try:
        close_code = ws.close_code
        close_reason = ws.close_reason
    except Exception:
        return None, None
    return close_code, close_reason


_static_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
