import struct

from app.tts_providers.google_provider import _linear16_pcm_payload


def _wav(pcm: bytes, *, extra_chunk: bytes = b"") -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 24_000, 48_000, 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if extra_chunk:
        chunks += b"JUNK" + struct.pack("<I", len(extra_chunk)) + extra_chunk
        if len(extra_chunk) % 2:
            chunks += b"\x00"
    chunks += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def test_google_linear16_wav_is_unwrapped_to_raw_pcm():
    pcm = b"\x01\x02\x03\x04"

    assert _linear16_pcm_payload(_wav(pcm, extra_chunk=b"x")) == pcm


def test_google_raw_pcm_is_left_unchanged():
    pcm = b"\x01\x02\x03\x04"

    assert _linear16_pcm_payload(pcm) == pcm
