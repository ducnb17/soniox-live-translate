# STT/TTS Refactor Summary

## Architecture

- `frontend/src/speech-to-text.ts` owns the STT WebSocket, microphone audio
  transport, transcript/translation events, reconnect state, and
  `isListening`.
- `frontend/src/text-to-speech.ts` owns TTS enablement, PCM playback, the audio
  queue, `isTtsEnabled`, and `isSpeaking`.
- `frontend/src/app.ts` is the one-way coordinator: STT events are rendered and
  forwarded to TTS only through callbacks. Neither module calls lifecycle
  methods on the other.

## Independent controls

- The `STT` button starts/stops listening or file transcription.
- The `TTS` button remains clickable whether STT is idle or active.
- Disabling TTS immediately clears local playback and sends `tts_control=false`
  to cancel backend synthesis. It does not close the STT socket or clear text.
- Enabling TTS sends `tts_control=true`; only subsequently completed translated
  lines are synthesized. Disabled-period lines are not buffered or replayed.

## Backend subscription

The WebSocket session keeps its TTS transport available, but
`backend/app/stt.py` gates queue insertion with the mutable TTS subscription.
This allows toggling spoken output without reconnecting STT and avoids TTS API
usage while the subscriber is disabled.

## Verification

- Frontend typecheck, unit tests, and production build pass.
- Backend `test_handle_stt.py` covers disabled lines continuing to reach the UI,
  re-enabling for new lines, and no replay of missed translations.
