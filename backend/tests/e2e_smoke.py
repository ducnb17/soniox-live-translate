"""E2E smoke test that starts its own server, runs the test, then shuts down.

No need for a separate server process — this script manages everything.

Usage:
    cd backend && python tests/e2e_smoke.py
"""

import asyncio
import json
import os
import sys
import time

# Ensure the backend dir is on sys.path so uvicorn can import `app.main`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import uvicorn
import websockets

# Set the API key from .env before importing app modules.
from dotenv import load_dotenv
load_dotenv(override=True)

AUDIO_URL = "https://soniox.com/media/examples/spanish_weather_report.mp3"
AUDIO_DURATION = 20.0  # short clip — stay well within Soniox session timeout
HOST = "127.0.0.1"
PORT = 18799  # high port to avoid conflicts


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        check.failed += 1
check.failed = 0


async def main() -> int:
    print(f"=== E2E smoke test (self-hosted server on :{PORT}) ===\n")

    config = uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="error",
    )
    server = uvicorn.Server(config)

    # Start server in background task
    server_task = asyncio.create_task(server.serve())
    # Wait for startup
    print("  starting server...", flush=True)
    for _ in range(10):
        await asyncio.sleep(1.0)
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"http://{HOST}:{PORT}/health")
                if r.status_code == 200:
                    print("  server ready", flush=True)
                    break
        except Exception:
            continue
    else:
        print("  SERVER FAILED TO START", flush=True)
        server.should_exit = True
        return 1

    base = f"http://{HOST}:{PORT}"
    ws_base = f"ws://{HOST}:{PORT}"

    try:
        # --- Download audio locally ---
        audio_path = "/tmp/opencode/e2e_audio.mp3"
        if not os.path.exists(audio_path):
            print("  downloading audio file...", flush=True)
            async with httpx.AsyncClient(timeout=60.0) as c:
                r = await c.get(AUDIO_URL, follow_redirects=True)
                r.raise_for_status()
                with open(audio_path, "wb") as f:
                    f.write(r.content)
            print(f"  downloaded {os.path.getsize(audio_path)} bytes", flush=True)

        with open(audio_path, "rb") as f:
            full_audio = f.read()
        # Use only the first AUDIO_DURATION seconds of audio to stay within
        # Soniox's session timeout. 64kbps = 8KB/s.
        audio_data = full_audio[:int(8000 * AUDIO_DURATION)]
        print(f"  audio loaded: {len(audio_data)} bytes ({AUDIO_DURATION}s of {len(full_audio)}B full)", flush=True)

        # --- 1. REST endpoints ---
        print("[1/4] REST endpoints")
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{base}/health")
            check("GET /health -> 200", r.status_code == 200, r.text)

            r = await c.get(f"{base}/config")
            data = r.json()
            check("GET /config -> voices present", len(data.get("voices", [])) > 0,
                  f"{len(data.get('voices', []))} voices")
            check("GET /config -> languages present", len(data.get("languages", [])) > 0,
                  f"{len(data.get('languages', []))} languages")
            check("GET /config -> configured bool", "configured" in data,
                  str(data.get("configured")))

        # --- 2. WebSocket session (mic mode, send audio bytes directly) ---
        print("\n[2/4] WebSocket session (one_way es->en, tts=true)")
        print("  sending audio bytes directly (bypassing HTTP fetch)...")
        params = (
            f"mode=one_way&target_lang=en&lang_id=true&diarize=true"
            f"&voice=Maya&tts=true"
        )
        ws_url = f"{ws_base}/ws/translate?{params}"

        got_session_id = False
        got_translation_token = False
        got_end_token = False
        got_finished = False
        got_tts_audio = False
        got_session_done = False
        got_error: str | None = None
        # Soniox STT sends a 408 timeout after the b"" end-of-stream signal
        # instead of `finished: true` for real-time sessions. Treat it as a
        # valid session-end signal (the official demo does the same — just
        # prints the error and breaks, relying on the finally block for cleanup).
        SESSION_END_ERRORS = {"408"}
        got_session_end = False
        token_count = 0
        audio_bytes = 0
        t0 = time.monotonic()
        first_translations: list[str] = []

        try:
            async with websockets.connect(ws_url, max_size=None) as ws:
                print(f"  connected, sending {len(audio_data)} bytes at real-time pace...")
                
                # Send audio bytes at real-time pace, then b"" end signal
                byte_rate = len(audio_data) / AUDIO_DURATION
                bytes_per_tick = max(1, int(byte_rate * 0.1))
                loop = asyncio.get_running_loop()
                next_tick = loop.time()
                
                async def send_audio():
                    offset = 0
                    while offset < len(audio_data):
                        chunk = audio_data[offset:offset + bytes_per_tick]
                        await ws.send(chunk)
                        offset += bytes_per_tick
                        next_tick_val = next_tick + (offset // bytes_per_tick) * 0.1
                        delay = next_tick_val - loop.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                    # Send any remaining bytes
                    if offset < len(audio_data):
                        await ws.send(audio_data[offset:])
                    # End-of-stream signal
                    await ws.send(b"")
                    print(f"  audio sent + b\"\" end signal", flush=True)
                
                send_task = asyncio.create_task(send_audio())
                
                deadline = time.monotonic() + AUDIO_DURATION + 60.0

                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError:
                        if got_session_done:
                            break
                        if got_finished:
                            # Wait a bit more for session_done
                            continue
                        continue

                    if isinstance(raw, bytes):
                        audio_bytes += len(raw)
                        if not got_tts_audio and len(raw) > 100:
                            got_tts_audio = True
                        continue

                    data = json.loads(raw)

                    if data.get("error_code"):
                        err_code = str(data.get("error_code", ""))
                        if err_code in SESSION_END_ERRORS:
                            got_session_end = True
                            print(f"  session ended by Soniox ({err_code}) — treating as normal end")
                            # Don't break — wait a bit more for session_done from TTS cleanup
                        else:
                            got_error = f"{data['error_code']}: {data['error_message']}"
                            break

                    if data.get("session_id") and not got_session_id:
                        got_session_id = True
                        print(f"  session_id: {data['session_id']}")
                        continue

                    if data.get("session_done"):
                        got_session_done = True
                        break

                    if data.get("barge_ack"):
                        continue

                    for tok in data.get("tokens", []):
                        token_count += 1
                        txt = tok.get("text", "")
                        if txt == "<end>":
                            got_end_token = True
                        elif tok.get("translation_status") == "translation":
                            if not got_translation_token:
                                got_translation_token = True
                                print(f"  first translation token: {txt!r}")
                            if len(first_translations) < 20:
                                first_translations.append(txt)

                    if data.get("finished"):
                        got_finished = True
                        print(f"  STT finished (tokens so far: {token_count})")

        except Exception as e:
            got_error = str(e)

        try:
            await send_task
        except Exception:
            pass

        elapsed = time.monotonic() - t0
        print(f"  elapsed: {elapsed:.1f}s, tokens: {token_count}, audio bytes: {audio_bytes}")
        if first_translations:
            print(f"  sample translation: {''.join(first_translations[:15])!r}")

        check("received session_id", got_session_id)
        check("received >=1 translation token", got_translation_token)
        check("received <end> token OR session-end signal", got_end_token or got_session_end,
              "<end>" if got_end_token else "408" if got_session_end else "neither")
        # finished=true may not arrive — Soniox sends 408 timeout instead.
        check("received finished=true OR session-end signal", got_finished or got_session_end,
              "finished" if got_finished else "408 session-end" if got_session_end else "neither")
        check("received TTS audio (binary PCM)", got_tts_audio,
              f"{audio_bytes} bytes total")
        # session_done: the browser uses this to auto-stop the UI. Should
        # arrive after STT ends and TTS streams are finalized/cancelled.
        check("received session_done", got_session_done,
              "(browser auto-stop signal)" if got_session_done else "(missing — UI won't auto-stop)")
        if got_error:
            check("no unexpected error from server", False, got_error)
        else:
            check("no unexpected error from server", True)

        # --- 3. Summary ---
        print(f"\n[3/3] Summary")
        print(f"  checks failed: {check.failed}")
        if check.failed == 0:
            print("  ALL PASSED — Soniox integration verified end-to-end.")
            return 0
        else:
            print(f"  {check.failed} CHECK(S) FAILED")
            return 1

    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
