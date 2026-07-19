# soniox-live-translate

Real-time speech-to-speech translation built on the **Soniox Live** APIs
(real-time STT + translation + real-time TTS), with barge-in / turn-taking.

Two WebSocket streams chained by a small FastAPI proxy:

```
Browser (mic / file)
   │  audio bytes (binary)
   ▼
FastAPI /ws/translate ──► Soniox STT+translation (wss://stt-rt.soniox.com)
   │                          │ tokens (translation_status:"translation", <end>)
   │                          ▼
   │  translation text ──►  TTS queue ──► Soniox TTS (wss://tts-rt.soniox.com)
   │                          │   one stream per direction, multiplexed by stream_id
   │                          ▼
   ◄── PCM s16le @ 24kHz ────┘   → Web Audio API playback
```

## Features

- **One-way** (detect speech → translate to target) and **two-way**
  (bilingual conversation, both languages spoken back).
- **Per-utterance TTS streams** pre-warmed per direction for low first-word
  latency. A single TTS WebSocket hosts up to 2 concurrent streams using
  Soniox's `stream_id` multiplexing.
- **Barge-in / turn-taking:** the browser VAD (RMS on the mic stream)
  interrupts local playback instantly and asks the backend to cancel the
  currently-open Soniox TTS streams (`{"stream_id":...,"cancel":true}`). The
  backend also drains queued text and bumps a "barge epoch" so stale tokens
  are dropped.
- **Diarization & language identification** toggleable; rendered inline.
- **Custom context / glossary** (domain, terms, translation_terms) via JSON
  textarea → base64 → STT config `context` block.
- **Transcript download (client-side):** JSON / CSV export from the sidebar
  — no PII roundtrip. Server also persists `backend/transcripts/<id>.json`
  and exposes `GET /transcript/{id}`.
- **Dark mode** toggle (saved to localStorage; respects OS preference).
- **First-run setup page** (`/setup`) — persists the API key to
  `%APPDATA%\SonioxLiveTranslate\config.json` (Windows), never bundled into
  the installer.
- **Windows installer** — system-tray desktop app built through the single
  supported desktop release pipeline, `.github/workflows/release.yml`
  (push a `v*.*.*` tag → installer appears on the Releases page).
- TypeScript + Web Audio frontend built with Vite.

## Requirements

- Python 3.13+
- A Soniox API key (https://console.soniox.com)
- A browser with microphone access (for mic mode)

## Setup

### Prerequisites

- Python 3.13+
- Node.js 20+ and pnpm 9+ (for the frontend build)
- A Soniox API key (https://console.soniox.com)
- A browser with microphone access (for mic mode)

### Development (hot reload)

Two terminals — Vite dev server (frontend, port 5173) proxies to FastAPI
(backend, port 8765):

```bash
# Terminal 1: backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit .env and put your SONIOX_API_KEY
uvicorn app.main:app --reload --port 8765

# Terminal 2: frontend (hot reload via Vite)
cd frontend
pnpm install
pnpm dev
```

Open <http://localhost:5173>. Vite proxies `/health`, `/config`, `/setup`,
`/transcript`, and `/ws` to the backend on 8765.

### Production / desktop build

```bash
cd frontend && pnpm install && pnpm build   # → frontend/dist/
cd ../backend && uvicorn app.main:app --port 8765
```

Open <http://localhost:8765>. The backend serves `frontend/dist/` if it
exists, otherwise falls back to `frontend/` (source).

For mic mode: click the mode-toggle (bottom-left) to switch from "Play audio
file" to "Start talking", then grant the browser microphone permission.

## Usage

1. **Pick mode** (One-way / Two-way).
2. **Pick languages**: target (one-way) or the conversation pair (two-way).
3. **Pick voices**: voice speaks the B-side translation (speaker B's
   language); `voice_b` speaks the A-side translation (two-way only).
4. **Settings**: diarization, language id, spoken translation, barge-in.
5. **Custom context** (optional): JSON with `general`, `text`, `terms`,
   `translation_terms` keys.
6. **Start talking** (mic) or **Play audio file** (URL sample provided).
7. Stop; the transcript is saved server-side under your `session_id`.

## API

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness probe |
| `GET /config` | Static `{voices, languages, configured}` option lists |
| `GET /setup` | First-run setup HTML page |
| `GET /setup/status` | `{configured: bool}` |
| `POST /setup` | `{soniox_api_key, host?, port?}` — persist to user config |
| `GET /transcript/{session_id}` | Persisted session transcript JSON |
| `WS  /ws/translate` | Proxy between browser and Soniox STT+TTS |

### WebSocket query params

| Param | Default | Notes |
|---|---|---|
| `mode` | `one_way` | `one_way` or `two_way` |
| `target_lang` | `en` | one-way target; ignored-ish in two-way (acts as speakable direction) |
| `lang_a`, `lang_b` | — | two-way conversation pair |
| `lang_id` | true | enable language identification |
| `diarize` | true | enable speaker diarization |
| `voice` | `Maya` | voice for lang_b (two-way) / single direction (one-way) |
| `voice_b` | — | voice for lang_a direction (two-way) |
| `tts` | true | enable TTS playback |
| `context_b64` | — | base64-UTF8 JSON for STT `context` block |
| `audio_url`, `audio_duration` | — | file test mode |

## Project layout

```
backend/
├─ app/
│  ├─ config.py           # env, VOICES, LANGUAGES, constants, set_api_key
│  ├─ config_store.py     # %APPDATA% config.json read/write
│  ├─ context_builder.py  # STT config + context normalization
│  ├─ stt.py              # ingress pipe (binary→STT, text→control), handle_stt
│  ├─ tts.py             # multi-direction streams, barge-in, cancel, keepalive
│  ├─ transcript.py       # TranscriptStore (in-memory + JSON file)
│  ├─ logging_config.py   # structlog + rotating file log
│  └─ main.py             # FastAPI app + /setup routes
├─ tests/                  # pytest: context, routing, reconnect, DB, TTS, API
├─ .env / .env.example
├─ transcripts/            # JSON files per session
└─ pytest.ini
frontend/
├─ src/
│  ├─ app.ts              # main app: recorder, Web Audio PCM, VAD, download
│  └─ types.ts            # Soniox token/response types, constants
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
# → installer_output/SonioxLiveTranslate-Setup-0.2.1.exe
```

### Build via GitHub Actions

Push a tag:

```bash
git tag v0.2.1
git push origin v0.2.1
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
  `BARGE_RMS_THRESHOLD` / `BARGE_HOLD_MS` in `app.js` if needed.
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
