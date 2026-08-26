"""HTTP providers for locally installed Vietnamese neural-TTS bridges.

These providers keep large voice models outside the Electron installer. A local
bridge owns its model/GPU process and exposes ``/health``, ``/voices`` and
``/tts``. Soniox receives 16-bit WAV and converts it to its fixed PCM 24 kHz
playback format sentence by sentence.
"""
from __future__ import annotations

import os
import struct
import wave
from io import BytesIO
from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider


def _resample_s16le_mono(pcm: bytes, source_rate: int, target_rate: int = 24_000) -> bytes:
    if source_rate == target_rate:
        return pcm
    if source_rate <= 0 or len(pcm) % 2:
        raise ValueError("invalid 16-bit mono PCM")
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    if len(samples) < 2:
        return pcm
    count = round(len(samples) * target_rate / source_rate)
    out: list[int] = []
    for index in range(count):
        position = index * source_rate / target_rate
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        out.append(round(samples[left] + (samples[right] - samples[left]) * (position - left)))
    return struct.pack(f"<{len(out)}h", *out)


class _LocalVietnameseBase(TTSProviderBase):
    provider_id = ""
    provider_name = ""
    env_name = ""
    default_url = ""
    description = ""
    fallback_voices: tuple[tuple[str, str], ...] = ()

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _url(self) -> str:
        return os.environ.get(self.env_name, self.default_url).rstrip("/")

    async def test_connection(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self._url()}/health")
                response.raise_for_status()
                health = response.json()
            if health.get("ok") or health.get("status") == "ok":
                return True, f"OK — {health.get('engine', health.get('backend', 'local model'))}"
            return False, f"Local server is not ready: {health}"
        except Exception as exc:
            return False, f"Cannot connect to local service at {self._url()}: {exc}"

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        # The app may ask with its initial UI language (often "en") before the
        # user changes the translation target. These are Vietnamese voice packs,
        # so always expose the catalog rather than rendering an empty selector.
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{self._url()}/voices")
                response.raise_for_status()
                items = response.json()
        except Exception:
            items = [{"id": voice_id, "name": name} for voice_id, name in self.fallback_voices]
        return [
            Voice(
                id=str(item["id"]),
                name=str(item.get("name") or item.get("description") or item["id"]),
                language="vi",
                gender="neutral",
                provider_id=self.provider_id,
            )
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if lang and not lang.lower().startswith("vi"):
            raise ValueError(f"{self.provider_name} only supports Vietnamese")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(f"{self._url()}/tts", json={"text": text, "voice_id": voice_id or None})
                response.raise_for_status()
                wav_bytes = response.content
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:240]
            raise RuntimeError(f"{self.provider_id} /tts HTTP {exc.response.status_code}: {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"{self.provider_id} request failed: {exc}") from exc
        try:
            with wave.open(BytesIO(wav_bytes), "rb") as reader:
                channels, width, rate = reader.getnchannels(), reader.getsampwidth(), reader.getframerate()
                pcm = reader.readframes(reader.getnframes())
        except (wave.Error, EOFError) as exc:
            raise RuntimeError(f"{self.provider_id} returned an invalid WAV: {exc}") from exc
        if channels != 1 or width != 2:
            raise RuntimeError(f"{self.provider_id} must return 16-bit mono WAV")
        pcm = _resample_s16le_mono(pcm, rate)
        if not pcm:
            raise RuntimeError(f"{self.provider_id} returned empty audio")
        for offset in range(0, len(pcm), 4096):
            yield pcm[offset:offset + 4096]

    def estimate_cost(self, char_count: int) -> float:
        return 0.0

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id=self.provider_id,
            name=self.provider_name,
            description=self.description,
            requires_api_key=False,
            supports_streaming=False,
            tier="free",
            pricing_url="",
            approximate_cost_per_1m_chars=0.0,
        )


@register_provider
class VieNeuTTSProvider(_LocalVietnameseBase):
    provider_id = "vieneu_tts"
    provider_name = "VieNeu-TTS v3 Turbo (local GPU)"
    env_name = "VIENEU_TTS_URL"
    default_url = "http://localhost:8768"
    description = "Vietnamese neural voices from a local VieNeu-TTS v3 Turbo GPU bridge; no API key or per-character cost."
    # Keep the selector useful even if the local bridge is restarting. This
    # mirrors VieNeu v3 Turbo's complete preset catalog (id, visible label).
    fallback_voices = (
        ("Minh Đức", "Minh Đức — Nam · Bắc · Phong cách tin tức"),
        ("Phạm Tuyên", "Phạm Tuyên — Nam · Bắc · Phong cách tự nhiên"),
        ("Thái Sơn", "Thái Sơn — Nam · Nam · Phong cách kể chuyện"),
        ("Xuân Vĩnh", "Xuân Vĩnh — Nam · Nam · Phong cách tự nhiên"),
        ("Thanh Bình", "Thanh Bình — Nam · Bắc · Phong cách kể chuyện"),
        ("Trúc Ly", "Trúc Ly — Nữ · Bắc · Phong cách tự nhiên"),
        ("Ngọc Linh", "Ngọc Linh — Nữ · Bắc · Phong cách kể chuyện"),
        ("Đoan Trang", "Đoan Trang — Nữ · Bắc · Phong cách tự nhiên"),
        ("Mai Anh", "Mai Anh — Nữ · Bắc · Phong cách tin tức"),
        ("Thục Đoan", "Thục Đoan — Nữ · Nam · Phong cách kể chuyện"),
        ("Minh Triết", "Minh Triết — Nam · Nam · Phong cách tin tức"),
        ("Thùy Dung", "Thùy Dung — Nữ · Nam · Phong cách tin tức"),
        ("Quang Sơn", "Quang Sơn — Nam · Trung · Phong cách tự nhiên"),
        ("Ngọc Trân", "Ngọc Trân — Nữ · Trung · Phong cách tự nhiên"),
        ("Mỹ Duyên", "Mỹ Duyên — Nữ · Nam · Phong cách đọc truyện"),
        ("Quỳnh Anh", "Quỳnh Anh — Nữ · Bắc · Phong cách đọc truyện"),
        ("Đức Trí", "Đức Trí — Nam · Nam · Phong cách đọc truyện"),
        ("Kim Thanh", "Kim Thanh — Nữ · Nam · Phong cách đọc truyện"),
        ("Ngọc Huyền", "Ngọc Huyền — Nữ · Bắc · Giọng đọc tự nhiên"),
        ("Adam", "Adam — Nam · Nam · Giọng đọc tự nhiên"),
    )


@register_provider
class ViXTTSProvider(_LocalVietnameseBase):
    provider_id = "vixtts"
    provider_name = "viXTTS voice clone (local GPU)"
    env_name = "VIXTTS_URL"
    default_url = "http://localhost:8769"
    description = "Vietnamese viXTTS local GPU voice-cloning bridge. Includes only reference voices explicitly configured on the local service."
    fallback_voices = (("vixtts_sample_female", "viXTTS sample — Nữ, clone giọng (GPU)"),)
