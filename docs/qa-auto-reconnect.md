# Auto-reconnect manual QA

Use this scenario after the automated reconnect tests to verify the complete
browser-to-backend-to-Soniox recovery path.

1. Start the backend and frontend, select Microphone mode, and start a live
   session. Speak until at least two finalized transcript lines are visible.
2. Disable the machine's external network adapter for 2–3 seconds. Keep the
   browser and local backend running, and continue speaking during the outage.
3. Confirm that the existing transcript remains visible and the status changes
   to `Đang kết nối lại… (lần x/5)` rather than showing a raw WebSocket error.
4. Re-enable the network before attempt 5. Confirm the status reports a
   successful reconnect, buffered speech continues to be processed, and the
   transcript from step 1 is still present.
5. Repeat the interruption but leave the network disabled through all five
   attempts. Confirm the UI shows the `Thử lại` button. In browser DevTools,
   verify the local WebSocket closes with code `4000` and reason
   `stt_reconnect_exhausted`.
6. Re-enable the network and click `Thử lại`. Confirm a new backend connection
   opens without clearing the existing transcript, and newly spoken text is
   appended to it.
7. To exercise overflow visibly in a development build, temporarily lower
   `MAX_RECONNECT_AUDIO_BUFFER_BYTES`, repeat step 2 while speaking, and confirm
   the transcript receives a `[mất âm thanh ... buffer đầy]` marker. Restore
   the constant afterward. The same retention/drop calculation is covered by
   `test_audio_buffer_keeps_newest_bytes_and_reports_overflow`.
8. Inspect structured logs and the conversation's `connection_events`: each
   disconnect must include its timestamp, actual close code/reason, retry
   count, and downtime; successful recovery must include total downtime and
   buffered/dropped byte counts.

Automated unexpected-close scenario:

```bash
cd backend
pytest -q tests/test_reconnect.py::test_unexpected_stt_close_recovers_without_losing_transcript
```
