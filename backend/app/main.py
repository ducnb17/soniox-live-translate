"""FastAPI app: WebSocket proxy between the browser and the two Soniox
real-time APIs (STT+translation and TTS), plus a REST endpoint for fetching
saved transcripts.
"""

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager

import websockets
from dotenv import load_dotenv
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import LANGUAGES, SONIOX_API_KEY, STT_URL, TTS_URL, VOICES, is_configured, set_api_key
from .context_builder import build_stt_config
from .stt import handle_stt, pipe_browser_to_stt, stream_url_to_stt
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
from .logging_config import configure_logging, get_logger

load_dotenv(override=True)
configure_logging()
log = get_logger("main")

transcript_store = TranscriptStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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
) -> None:
    """Open a translation session: browser <-> Soniox STT+TTS over WebSocket.

    The browser streams raw audio bytes (binary frames) to STT, and the
    backend proxies tokens + synthesized PCM back. Text frames from the
    browser ({"type":"utterances",...} / {"type":"barge"}) are intercepted
    by the single ingress coroutine to avoid two receivers racing.

    Two-way mode: `lang_a` and `lang_b` describe the conversation pair. TTS
    runs in both directions — Speaker A's utterances are spoken in `lang_b`
    (with `voice_b` if provided else `voice`), Speaker B's in `lang_a` (with
    `voice`). One-way: a single TTS direction `target_lang` with `voice`.
    """
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

    # Resolve the active TTS directions and per-direction voices.
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

    tts_queue: asyncio.Queue | None = asyncio.Queue() if tts else None
    tts_state: dict = new_tts_state(directions)

    stt_ws = None
    tts_ws = None

    try:
        stt_ws = await websockets.connect(STT_URL)
        await stt_ws.send(json.dumps(stt_config))

        if audio_url and audio_duration:
            input_coro = stream_url_to_stt(
                audio_url=audio_url,
                duration=audio_duration,
                browser_ws=browser_ws,
                stt_ws=stt_ws,
            )
        else:
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

            input_coro = pipe_browser_to_stt(
                browser_ws=browser_ws, stt_ws=stt_ws, on_text=on_text
            )

        if tts:
            tts_ws = await websockets.connect(TTS_URL)
            # Pre-warm every direction's TTS stream so the first utterance in
            # each direction doesn't pay the round-trip for stream setup.
            for direction in directions:
                await prewarm_stream(
                    tts_ws=tts_ws,
                    tts_state=tts_state,
                    direction=direction,
                    voice=direction_voices[direction],
                )

        async with asyncio.TaskGroup() as tg:
            tg.create_task(input_coro)
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
                )
            )
            if tts:
                tg.create_task(
                    tts_sender(
                        tts_queue=tts_queue,
                        tts_state=tts_state,
                        tts_ws=tts_ws,
                        direction_voices=direction_voices,
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
        pass
    finally:
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
        session.close()
        transcript_store.finish(session)


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
# Prefer the Vite build output (frontend/dist/) if it exists; fall back to
# the source frontend/ for dev and the no-build-step vanilla fallback.
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
_dist_dir = os.path.join(_frontend_dir, "dist")
_static_dir = _dist_dir if os.path.isdir(_dist_dir) else _frontend_dir
app.mount(
    "/",
    StaticFiles(directory=_static_dir, html=True),
    name="static",
)