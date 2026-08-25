"""Local Piper GPU TTS provider.

Connects to the GPU inference server running on the local network (default:
http://localhost:8767). The server exposes:

  POST /tts  {"text": "...", "voice_id": "<optional>", "rate": <optional float>}
             → audio/wav (Piper GPU synthesised)

  GET  /health → {"ok": true, ...}

This provider downloads the WAV, strips the 44-byte header, resamples to
PCM s16le 24 kHz mono via ffmpeg (same pipeline as EdgeTTSProvider), and
yields chunks so the TTS pipeline can start playback before the full
utterance is received.

Configuration
-------------
PIPER_GPU_URL  (env var, default "http://localhost:8767")
    Base URL of the GPU inference server. Override to point at a remote host.
"""

from __future__ import annotations

import asyncio
import os
import struct
import wave
from io import BytesIO
from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger

log = get_logger("piper_gpu_tts")

_DEFAULT_BASE_URL = "http://localhost:8767"

# Vietnamese voices served by the Piper GPU backend.
# voice_id is passed verbatim to POST /tts as "voice_id".
_VI_VOICES: list[tuple[str, str, str]] = [
    ("vi_VN-vais1000-medium", "Vais1000 (Nữ, GPU)", "female"),
    ("vi_VN-25hours_single-low", "25hours Single (Nữ, GPU)", "female"),
]

# Generic English fallback voices (Piper ships these by default).
_EN_VOICES: list[tuple[str, str, str]] = [
    ("en_US-lessac-medium", "Lessac (Female, US, GPU)", "female"),
    ("en_US-ryan-high", "Ryan (Male, US, GPU)", "male"),
]

_VOICES_BY_LANG: dict[str, list[tuple[str, str, str]]] = {
    "vi": _VI_VOICES,
    "en": _EN_VOICES,
    "en_us": _EN_VOICES,
    "en_gb": _EN_VOICES,
}


def _base_url() -> str:
    return os.environ.get("PIPER_GPU_URL", _DEFAULT_BASE_URL).rstrip("/")


def _resample_s16le_mono(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linearly resample signed 16-bit mono PCM without an external binary."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if len(pcm) % 2:
        raise ValueError("16-bit PCM must contain complete samples")
    if source_rate == target_rate:
        return pcm
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    if len(samples) < 2:
        return pcm
    output_count = round(len(samples) * target_rate / source_rate)
    output: list[int] = []
    for index in range(output_count):
        position = index * source_rate / target_rate
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output.append(round(samples[left] + (samples[right] - samples[left]) * fraction))
    return struct.pack(f"<{len(output)}h", *output)


@register_provider
class PiperGPUProvider(TTSProviderBase):
    """TTS via a local Piper-on-GPU inference server."""

    def __init__(self, api_key: str | None = None) -> None:
        # api_key unused — server requires no auth
        self._api_key = api_key

    # ── connection check ──────────────────────────────────────────────────────

    async def test_connection(self) -> tuple[bool, str]:
        url = f"{_base_url()}/health"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if data.get("ok"):
                    tts_gpu = data.get("tts_gpu", False)
                    return True, f"OK — tts_gpu={tts_gpu}"
                return False, f"Server returned ok=false: {data}"
        except httpx.ConnectError as exc:
            return False, f"Cannot connect to GPU server at {_base_url()}: {exc}"
        except Exception as exc:
            return False, f"Health check failed: {exc}"

    # ── voice catalogue ───────────────────────────────────────────────────────

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        key = (lang or "vi").lower()
        pairs = _VOICES_BY_LANG.get(key, _VI_VOICES)
        return [
            Voice(
                id=vid,
                name=name,
                language=lang or "vi",
                gender=gender,
                provider_id="piper_gpu",
            )
            for vid, name, gender in pairs
        ]

    # ── synthesis ─────────────────────────────────────────────────────────────

    async def synthesize_stream(
        self, text: str, voice_id: str, lang: str
    ) -> AsyncIterator[bytes]:
        """POST /tts → WAV bytes → PCM s16le 24 kHz mono via ffmpeg pipeline."""
        base = _base_url()
        payload: dict[str, object] = {"text": text}
        if voice_id:
            payload["voice_id"] = voice_id

        # Fetch the full WAV from the GPU server (typically <1 s on localhost).
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{base}/tts", json=payload)
                resp.raise_for_status()
                wav_bytes = resp.content
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"[piper_gpu] Cannot reach GPU server at {base}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"[piper_gpu] /tts returned HTTP {exc.response.status_code}"
            ) from exc

        if not wav_bytes:
            raise RuntimeError("[piper_gpu] Empty WAV response from GPU server")

        # Decode/resample in-process.  The former ffmpeg subprocess made every
        # Piper request depend on a binary that the packaged Windows backend did
        # not include; its FileNotFoundError was swallowed by the async sender,
        # leaving a registered line with no usable audio and stalling playback.
        try:
            with wave.open(BytesIO(wav_bytes), "rb") as reader:
                channels = reader.getnchannels()
                sample_width = reader.getsampwidth()
                sample_rate = reader.getframerate()
                frames = reader.readframes(reader.getnframes())
        except (wave.Error, EOFError) as exc:
            raise RuntimeError(f"[piper_gpu] Invalid WAV response: {exc}") from exc
        if channels != 1 or sample_width != 2:
            raise RuntimeError(
                f"[piper_gpu] Expected 16-bit mono WAV, got {sample_width * 8}-bit/{channels}ch"
            )
        pcm = _resample_s16le_mono(frames, sample_rate, 24000)
        if not pcm:
            raise RuntimeError("[piper_gpu] WAV response contained no PCM frames")
        for offset in range(0, len(pcm), 4096):
            yield pcm[offset:offset + 4096]

    # ── cost / metadata ───────────────────────────────────────────────────────

    def estimate_cost(self, char_count: int) -> float:
        return 0.0  # local server, no per-character charge

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="piper_gpu",
            name="Local Piper (GPU, free)",
            description=(
                "Local Piper TTS running on a dedicated GPU server "
                f"({_base_url()}). No API key, no cost, low latency on LAN. "
                "Best for Vietnamese and English voices."
            ),
            requires_api_key=False,
            supports_streaming=True,
            tier="free",
            pricing_url="",
            approximate_cost_per_1m_chars=0.0,
        )
