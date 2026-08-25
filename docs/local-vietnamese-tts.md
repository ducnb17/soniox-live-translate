# Local Vietnamese neural TTS bridges

Soniox Live Translate v2.1.0 can use three local Vietnamese TTS providers. They are **not bundled in the Windows installer**: neural model files and CUDA runtimes are too large and platform-specific. The Electron app calls a local HTTP bridge; each translated sentence remains one synthesis job and is resampled to the app's PCM 24 kHz playback contract.

## Available providers

| Provider shown in Soniox | Local endpoint | Purpose |
|---|---:|---|
| Local Piper (GPU, free) | `http://localhost:8767` | Fast offline Vietnamese reading; 3 installed Piper voices. |
| VieNeu-TTS v3 Turbo (local GPU) | `http://localhost:8768` | 20 built-in Vietnamese voices with regional/style metadata. |
| viXTTS voice clone (local GPU) | `http://localhost:8769` | A configured, consented reference voice. The default bridge exposes only the project sample voice. |

## Start the installed bridges (WSL/Linux)

```bash
# Piper service
~/tts-stt-server/start.sh

# VieNeu-TTS v3 Turbo
cd ~/VieNeu-TTS
uv run python -m apps.soniox_api

# viXTTS
cd ~/vixtts-demo
.venv/bin/python soniox_api.py
```

The first start loads/downloads models and can take time. The services bind to loopback only. Select the corresponding provider under **Settings → TTS** in Soniox; its voice menu is populated from the service.

Optional environment variables override endpoints:

```bash
export PIPER_GPU_URL=http://localhost:8767
export VIENEU_TTS_URL=http://localhost:8768
export VIXTTS_URL=http://localhost:8769
```

## Voice-cloning safety

Use viXTTS only with a recording of your own voice or a voice for which the speaker gave permission. The Soniox bridge does not offer uploads or retain reference recordings; manage enrolled voices in viXTTS's own local interface.

## `vietnamese-tts-local` status

The separate `hoangtung386/vietnamese-tts-local` project was installed and its source tests pass, but its configured NeuCodec ONNX decoder is Hugging Face-gated. It cannot become a working Soniox provider until the account that downloads models has accepted the model's access terms and authenticated with Hugging Face. It is deliberately not listed as usable in Soniox until that prerequisite is satisfied.

## Windows release note

The GitHub Windows installer contains the Soniox application and its provider adapters. It does **not** contain WSL/Linux Python environments or multi-GB local models. Use a locally reachable bridge (for example this WSL setup) before selecting a local neural provider.
