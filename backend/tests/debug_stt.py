"""Debug: dump raw STT messages from Soniox to see the finished signal."""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import websockets
from dotenv import load_dotenv

load_dotenv(override=True)

SONIOX_API_KEY = os.environ["SONIOX_API_KEY"]
STT_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
AUDIO_URL = "https://soniox.com/media/examples/spanish_weather_report.mp3"
AUDIO_DURATION = 14.0


async def main():
    print("=== Direct STT debug ===\n", flush=True)
    
    stt_config = {
        "api_key": SONIOX_API_KEY,
        "model": "stt-rt-v5",
        "audio_format": "auto",
        "enable_endpoint_detection": True,
        "max_endpoint_delay_ms": 500,
        "enable_speaker_diarization": True,
        "enable_language_identification": True,
        "translation": {"type": "one_way", "target_language": "en"},
    }

    stt_ws = await websockets.connect(STT_URL)
    await stt_ws.send(json.dumps(stt_config))
    print("config sent, streaming audio...", flush=True)

    # Stream audio at real-time pace
    async def stream_audio():
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("GET", AUDIO_URL, follow_redirects=True) as resp:
                content_length = int(resp.headers.get("content-length", 0))
                byte_rate = content_length / AUDIO_DURATION if content_length else 16000
                bytes_per_tick = max(1, int(byte_rate * 0.1))
                buffer = bytearray()
                loop = asyncio.get_running_loop()
                next_tick = loop.time()
                async for chunk in resp.aiter_bytes():
                    buffer.extend(chunk)
                    while len(buffer) >= bytes_per_tick:
                        await stt_ws.send(bytes(buffer[:bytes_per_tick]))
                        del buffer[:bytes_per_tick]
                        next_tick += 0.1
                        delay = next_tick - loop.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                if buffer:
                    await stt_ws.send(bytes(buffer))
                await stt_ws.send(b"")  # end-of-stream signal
                print(f"\n[audio] stream done, sent b\"\" end signal", flush=True)

    # Receive messages and dump keys
    msg_count = 0
    last_keys = set()
    t0 = time.monotonic()
    
    stream_task = asyncio.create_task(stream_audio())
    
    try:
        while True:
            try:
                msg = await asyncio.wait_for(stt_ws.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                print(f"\n[recv] 30s timeout — no more messages. elapsed={time.monotonic()-t0:.1f}s", flush=True)
                break
            
            msg_count += 1
            data = json.loads(msg)
            keys = set(data.keys())
            
            # Print every message's keys, and full content for special ones
            if keys != last_keys or msg_count % 50 == 0:
                print(f"[msg {msg_count}] keys={sorted(keys)}", flush=True)
                last_keys = keys
            
            if data.get("finished"):
                print(f"[msg {msg_count}] FINISHED! full={json.dumps(data, indent=2)}", flush=True)
                break
            if data.get("error_code"):
                print(f"[msg {msg_count}] ERROR! {data}", flush=True)
                break
            if msg_count <= 3 or msg_count % 100 == 0:
                # Show first few and milestones
                tokens = data.get("tokens", [])
                print(f"  tokens: {len(tokens)}", flush=True)
    
    except websockets.ConnectionClosed as e:
        print(f"\n[recv] WS closed: {e}", flush=True)
    
    print(f"\ntotal messages: {msg_count}, elapsed: {time.monotonic()-t0:.1f}s", flush=True)
    await stt_ws.close()


if __name__ == "__main__":
    asyncio.run(main())
