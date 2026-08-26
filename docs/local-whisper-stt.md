# Whisper STT local GPU

Soniox Live Translate can use **Whisper Large-v3 (local GPU)** as its
Speech-to-Text provider. Audio stays on the local machine: the desktop app
sends 16 kHz mono PCM to its local backend, which calls the local GPU bridge at
`http://localhost:8767`.

## Requirements

The local bridge runs separately from the Windows installer because the model
and CUDA runtime are large. On the C246 WSL host:

```bash
cd ~/tts-stt-server
./start.sh
curl http://127.0.0.1:8767/health
```

The health response must contain `"stt_gpu": true`.

## Select it in the app

1. Open **Settings → Speech-to-Text**.
2. Select **Whisper Large-v3 (local GPU)**.
3. No API key is needed; press **Test** to verify the local service.
4. Save configuration, then start a session.

## Behaviour and limits

- The browser audio format is 16 kHz, mono, signed 16-bit PCM.
- The local adapter detects a short pause (about 0.75 seconds), transcribes the
  finished utterance, and then sends it through the existing translation/TTS
  pipeline.
- Whisper runs locally with GPU acceleration but is not a partial-token cloud
  streaming model. Expect text after each speech pause, not word-by-word
  interim captions.
- Language is auto-detected so both Vietnamese and multilingual input work.
- This is local-only: it does not require an STT API key. A separate cloud
  translation provider or local TTS provider can still be selected normally.

## Troubleshooting

If **Test** says it cannot connect:

```bash
curl http://127.0.0.1:8767/health
```

Restart the bridge if needed:

```bash
cd ~/tts-stt-server
./start.sh
```

Keep the bridge on the same machine as Soniox Live Translate. The default port
can be changed only by setting `LOCAL_WHISPER_STT_URL` for the Soniox backend.
Do not expose port 8767 to the public internet.
