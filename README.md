# soniox-live-translate

Real-time speech-to-speech translation built on the **Soniox Live** APIs
(real-time STT + translation + real-time TTS), with barge-in / turn-taking.

The browser frontend and FastAPI backend connect live STT/translation to the
selected TTS provider:

```
Browser (microphone / tab-system audio / URL file)
   │  audio bytes (binary)
   ▼
FastAPI /ws/stt ────────► Soniox STT+translation
   │                         │ final line_ready JSON
   ▼                         ▼
Transcript UI        Browser TTS controller ──► FastAPI /ws/tts
                                               │ selected TTS provider
                                               ▼
                               PCM s16le @ 24kHz → Web Audio API playback
```

## Features

- **One-way** (detect speech → translate to target) and **two-way**
  (bilingual conversation, both languages spoken back).
- **Per-utterance Soniox TTS streams** are pre-warmed per direction for low
  first-word latency. A single Soniox TTS WebSocket hosts up to 2 concurrent
  streams using `stream_id` multiplexing.
- **Barge-in / turn-taking:** the browser VAD (RMS on the mic stream)
  interrupts local playback instantly and asks the backend to cancel the
  currently-open Soniox TTS streams (`{"stream_id":...,"cancel":true}`). The
  backend also drains queued text and bumps a "barge epoch" so stale tokens
  are dropped.
- **Automatic STT reconnection:** capped exponential backoff with jitter,
  transcript preservation, buffered microphone audio, a visible reconnect
  counter, downtime markers when the buffer overflows, and a manual Retry
  button after all automatic attempts are exhausted.
- **Input/output device selection:** choose System Default or any physical or
  virtual device (including VB-Cable), hot-plug refresh via `devicechange`,
  saved selection with a visible fallback when a device disappears, a live
  microphone level meter, and a speaker test tone.
- **Conversation history:** finalized segments are stored in SQLite; the UI
  provides paginated history, FTS5 full-text search, TXT/SRT/JSON export,
  deletion, retention settings, storage statistics, and manual cleanup.
- **Seven TTS providers with Provider → Voice selection:** Soniox built-in,
  Google Cloud TTS (Chirp3 HD), OpenAI, Azure Neural TTS, ElevenLabs,
  Amazon Polly, and Deepgram Aura. External-provider failures visibly fall
  back to Soniox; synthesized audio is cached and estimated character cost is
  shown for the current session. Saved API keys are encrypted for the current
  Windows user with DPAPI.
- **Diarization & language identification** toggleable; rendered inline.
- **Custom context / glossary** (domain, terms, translation_terms) via JSON
  textarea → base64 → STT config `context` block.
- **Current transcript download:** JSON/CSV export remains available directly
  from the active session, alongside the persisted conversation history.
- **Microphone, tab/system audio, and URL/file inputs:** browser tab or screen
  audio capture uses `getDisplayMedia`; microphone capture supports barge-in.
- **Dark mode** toggle (saved to localStorage; respects OS preference).
- **First-run setup page** (`/setup`) — persists the API key to
  `%APPDATA%\SonioxLiveTranslate\config.json` encrypted with Windows DPAPI,
  never bundled into the installer.
- **Windows installer** — system-tray desktop app built through the single
  supported desktop release pipeline, `.github/workflows/release.yml`
  (push a `v*.*.*` tag → installer appears on the Releases page).
- **Strict TypeScript + Web Audio frontend:** Vite provides hot reload in
  development; `pnpm run build` runs `tsc --noEmit` before creating
  `frontend/dist/`.

## Requirements

- Python 3.13+
- Node.js 20+ and pnpm 9.12+ (frontend development/build)
- A Soniox API key (https://console.soniox.com)
- Chrome or Edge with microphone permission; tab/system capture and explicit
  speaker routing depend on browser/OS support
- Optional API keys for external TTS providers

## Setup

### Development (hot reload)

Two terminals — Vite dev server (frontend, port 5173) proxies to FastAPI
(backend, port 8765):

#### Terminal 1 — backend

Windows PowerShell:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and replace your_key_here with your SONIOX_API_KEY.
python -m uvicorn app.main:app --reload --port 8765
```

macOS/Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and replace your_key_here with your SONIOX_API_KEY.
python -m uvicorn app.main:app --reload --port 8765
```

Confirm the backend before starting Vite:

```text
http://127.0.0.1:8765/health  →  {"status":"ok"}
```

#### Terminal 2 — frontend

```bash
cd frontend
corepack enable
corepack prepare pnpm@9.12.0 --activate
pnpm install
pnpm run dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/health`, `/config`, `/setup`,
`/transcript`, `/api`, and `/ws` to the backend on port 8765, so setup,
history, retention, multi-TTS, and live translation use the same backend.

On Windows, the `/setup` page can save keys into the DPAPI-encrypted user
config. For cross-platform development, keep the Soniox key in `backend/.env`;
DPAPI-backed key persistence is intentionally Windows-only.

### Production / desktop build

```bash
cd frontend
pnpm install
pnpm run build   # tsc --noEmit + Vite → frontend/dist/
cd ../backend
python -m uvicorn app.main:app --port 8765
```

Open <http://127.0.0.1:8765>. The production server serves the compiled
`frontend/dist/`; run the frontend build before starting it.

For mic mode: click the mode-toggle (bottom-left) to switch from "Play audio
file" to "Start talking", then grant the browser microphone permission.

## Usage

1. **Pick mode** (One-way / Two-way).
2. **Pick languages**: target (one-way) or the conversation pair (two-way).
3. **Pick input/output devices**, then use Test Mic/Test Speaker if needed.
4. **Pick a TTS provider and voice**; add that provider's API key when asked.
5. **Settings**: diarization, language id, spoken translation, barge-in.
6. **Custom context** (optional): JSON with `general`, `text`, `terms`,
   `translation_terms` keys.
7. **Start talking**, share tab/system audio, or **Play audio file**.
8. Stop; open **Phiên đã lưu** to search, export, or clean up history.

## API

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness probe |
| `GET /config` | Static `{voices, languages, configured}` option lists |
| `GET /setup` | First-run setup HTML page |
| `GET /setup/status` | `{configured: bool}` |
| `POST /setup` | `{soniox_api_key, host?, port?}` — persist to user config |
| `GET /transcript/{session_id}` | Persisted session transcript JSON |
| `GET /api/conversations` | Paginated conversation history |
| `GET /api/conversations/search` | FTS5 conversation search |
| `GET /api/conversations/{id}` | Conversation details |
| `DELETE /api/conversations/{id}` | Delete a conversation |
| `GET /api/conversations/{id}/export` | Export `txt`, `srt`, or `json` |
| `GET /api/retention/stats` | Conversation database statistics |
| `POST /api/retention/cleanup` | Manually apply the selected retention period |
| `GET /api/tts/providers` | List the seven TTS providers |
| `GET /api/tts/providers/{id}/voices` | List voices for a provider/language |
| `GET /api/tts/config` | Read the selected provider/voice and masked key status |
| `POST /api/tts/config` | Update provider, voice, or encrypted API key |
| `WS  /ws/stt` | Browser audio to STT/translation JSON; never sends TTS audio |
| `WS  /ws/tts` | Independent configure/speak/cancel TTS command and PCM channel |
| `WS  /ws/translate` | Legacy combined-protocol compatibility endpoint |

### WebSocket query params

| Param | Default | Notes |
|---|---|---|
| `mode` | `one_way` | `one_way` or `two_way` |
| `target_lang` | `en` | one-way target; ignored-ish in two-way (acts as speakable direction) |
| `lang_a`, `lang_b` | — | two-way conversation pair |
| `lang_id` | true | enable language identification |
| `diarize` | true | enable speaker diarization |
| `context_b64` | — | base64-UTF8 JSON for STT `context` block |
| `audio_url`, `audio_duration` | — | file test mode |
| `input_device`, `output_device` | — | selected browser audio device IDs |
| `tts_provider` | `soniox` | selected TTS provider ID |

## Project layout

```
backend/
├─ app/
│  ├─ config.py           # env, VOICES, LANGUAGES, constants, set_api_key
│  ├─ config_store.py     # %APPDATA% config.json read/write
│  ├─ context_builder.py  # STT config + context normalization
│  ├─ db.py               # SQLite history, FTS5, retention, export
│  ├─ external_tts.py     # external synthesis, cache use, Soniox fallback
│  ├─ stt.py              # ingress pipe (binary→STT, text→control), handle_stt
│  ├─ tts.py              # Soniox streams, barge-in, cancel, keepalive
│  ├─ tts_provider.py     # seven-provider registry and bounded LRU cache
│  ├─ transcript.py       # TranscriptStore (in-memory + JSON file)
│  ├─ logging_config.py   # structlog + rotating file log
│  └─ main.py             # FastAPI app + /setup routes
├─ tests/                  # pytest: context, routing, reconnect, DB, TTS, API
├─ .env / .env.example
└─ pytest.ini
frontend/
├─ src/
│  ├─ app.ts              # UI, recorder, Web Audio PCM, reconnect handling
│  ├─ conversation-api.ts # paginated history/search/export REST client
│  ├─ device-selection.ts # saved device resolution and fallback logic
│  ├─ tts-usage.ts        # session character/cost aggregation
│  └─ types.ts            # Soniox token/response types and constants
├─ index.html             # main UI (Vite entry)
├─ setup.html             # first-run setup page
├─ styles.css             # light + dark themes + setup styles
├─ vite.config.ts         # Vite + dev proxy + setup.html copy plugin
├─ tsconfig.json
└─ dist/                   # build output (gitignored)
installer/
├─ launcher.py            # entry: load config → uvicorn → webbrowser → tray
├─ spec.spec              # PyInstaller onedir spec (bundled frontend/dist)
└─ installer.iss          # Inno Setup wizard (Program Files + shortcuts)
.github/workflows/
└─ release.yml            # only desktop release pipeline; v*.*.* tags
```

## Desktop build (Windows installer)

The Windows desktop app is a PyInstaller-bundled copy of the FastAPI server
plus a `pystray` system-tray launcher. **No API key is baked into the
installer** — on first launch the app opens a `/setup` page in the browser,
the user pastes their key, and it's persisted to
`%APPDATA%\SonioxLiveTranslate\config.json`.

### Build locally (Windows)

Requirements: Python 3.13+, [Inno Setup 6](https://jrsoftware.org/isdl.php).

```powershell
git clone <repo> && cd soniox-live-translate
pnpm --dir frontend install
pnpm --dir frontend build
python -m pip install -r backend\requirements.txt
python -m pip install pyinstaller pystray pillow
$env:PYTHONPATH = (Get-Location).Path
python -m PyInstaller installer\spec.spec --distpath dist_win --workpath build_win --noconfirm
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss
# → installer_output/SonioxLiveTranslate-Setup-0.3.1.exe
```

### Build via GitHub Actions

Push a tag:

```bash
git tag v0.3.1
git push origin v0.3.1
```

The `release.yml` workflow is the repository's only desktop release pipeline.
It runs on `windows-latest`, builds the PyInstaller + Inno Setup installer,
and attaches it to a new GitHub Release automatically.

### Run the desktop app

- Install → `SonioxLiveTranslate.exe` runs as a system-tray icon.
- First launch: browser opens `/setup` → paste API key → "Save and open app".
- Subsequent launches: tray icon → "Open app" / "Settings" / "Quit".

## Notes / caveats

- **Two-way multi-direction TTS** opens up to 2 concurrent Soniox streams on
  a single WebSocket connection (Soniox allows up to 5). Higher fan-out would
  need connection-level offload.
- The browser is the source of truth for final per-utterance text; the
  backend prompts a flush on each `<end>` token. Sanity-snapshot posting
  happens on Stop.
- Barge thresholds are tuned for a quiet environment; tweak
  `BARGE_RMS_THRESHOLD` / `BARGE_HOLD_MS` in `frontend/src/types.ts` if needed.
- Voice names are Soniox built-in voices ("Maya", "Adrian", …), multilingual
  across 60+ languages.
- **Tab/System audio mode:** when translating a shared tab/screen (e.g. a
  YouTube video), the browser has no API to attenuate the *source* tab's
  volume from the capturing page — unlike file-playback mode, where
  `fileAudio.volume` is lowered automatically once TTS starts. **Manually
  turn down the original tab/video's volume** before or during the session
  so the translated speech (TTS) stays intelligible instead of overlapping
  with the original audio.


## Roadmap

- M0–M4 — done (one-way / two-way / barge-in / context / transcript
  download / dark mode / first-run setup).
- M5 — done (structlog backend logging, pytest suite, Vite + TS
  frontend with strict typing).
- M6 — done (Windows installer: PyInstaller + Inno Setup + GitHub Actions
  + system tray + first-run prompt).
