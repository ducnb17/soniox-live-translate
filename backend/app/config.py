import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Mutable so the /setup first-run flow can update it in-process without a
# restart. Callers MUST read `config.SONIOX_API_KEY` (attribute access) or
# `get_api_key()` — never `from .config import SONIOX_API_KEY` then reuse.
SONIOX_API_KEY = os.environ.get("SONIOX_API_KEY", "")
STT_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
TTS_URL = "wss://tts-rt.soniox.com/tts-websocket"

STT_MODEL = "stt-rt-v5"
TTS_MODEL = "tts-rt-v1"
TTS_SAMPLE_RATE = 24000
TTS_AUDIO_FORMAT = "pcm_s16le"

# Keepalive cadence for the idle TTS WebSocket connection (seconds).
TTS_KEEPALIVE_INTERVAL = 20

# Keepalive cadence for the STT WebSocket connection (seconds).
# Soniox requires keepalive pings to prevent timeout disconnects (code 1011).
STT_KEEPALIVE_INTERVAL = 15

# Endpointing: fires the <end> token when the speaker pauses, letting us
# finalize the current TTS utterance stream quickly. Soniox STS example uses
# 500 ms for minimum end-to-end latency (see
# https://soniox.com/docs/translation/sts-translation). The frontend can
# override this per session via the `stt_delay_ms` query parameter.
MAX_ENDPOINT_DELAY_MS = 500
MIN_ENDPOINT_DELAY_MS = 200

def _user_data_dir() -> Path:
    """Per-user, always-writable data directory (mirrors config_store.py).

    Never write next to the executable/installation — on Windows that is
    typically Program Files, where a normal user has no write permission.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "SonioxLiveTranslate"


TRANSCRIPT_DIR = _user_data_dir() / "transcripts"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

VOICES = [
    "Adrian",
    "Claire",
    "Daniel",
    "Emma",
    "Grace",
    "Jack",
    "Kenji",
    "Maya",
    "Mina",
    "Nina",
    "Noah",
    "Owen",
]

LANGUAGES = [
    ("af", "Afrikaans"),
    ("sq", "Albanian"),
    ("ar", "Arabic"),
    ("az", "Azerbaijani"),
    ("eu", "Basque"),
    ("be", "Belarusian"),
    ("bn", "Bengali"),
    ("bs", "Bosnian"),
    ("bg", "Bulgarian"),
    ("ca", "Catalan"),
    ("zh", "Chinese"),
    ("hr", "Croatian"),
    ("cs", "Czech"),
    ("da", "Danish"),
    ("nl", "Dutch"),
    ("en", "English"),
    ("et", "Estonian"),
    ("fi", "Finnish"),
    ("fr", "French"),
    ("gl", "Galician"),
    ("de", "German"),
    ("el", "Greek"),
    ("gu", "Gujarati"),
    ("he", "Hebrew"),
    ("hi", "Hindi"),
    ("hu", "Hungarian"),
    ("id", "Indonesian"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("kn", "Kannada"),
    ("kk", "Kazakh"),
    ("ko", "Korean"),
    ("lv", "Latvian"),
    ("lt", "Lithuanian"),
    ("mk", "Macedonian"),
    ("ms", "Malay"),
    ("ml", "Malayalam"),
    ("mr", "Marathi"),
    ("no", "Norwegian"),
    ("fa", "Persian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("pa", "Punjabi"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("sr", "Serbian"),
    ("sk", "Slovak"),
    ("sl", "Slovenian"),
    ("es", "Spanish"),
    ("sw", "Swahili"),
    ("sv", "Swedish"),
    ("tl", "Tagalog"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("th", "Thai"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian"),
    ("ur", "Urdu"),
    ("vi", "Vietnamese"),
    ("cy", "Welsh"),
]


def languages_json() -> str:
    return json.dumps([{"code": c, "name": n} for c, n in LANGUAGES])


def voices_json() -> str:
    return json.dumps(VOICES)


def set_api_key(key: str) -> None:
    """Update the in-process API key (used by /setup first-run flow)."""
    global SONIOX_API_KEY
    SONIOX_API_KEY = key
    os.environ["SONIOX_API_KEY"] = key


def is_configured() -> bool:
    return bool(SONIOX_API_KEY) and SONIOX_API_KEY != "your_key_here"
