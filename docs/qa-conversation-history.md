# Conversation history end-to-end QA

## Automated coverage

Run the backend persistence/API tests and frontend REST integration tests:

```sh
cd backend
python -m pytest -q

cd ../frontend
npm test
npx tsc --noEmit
npx vite build
```

The backend tests use an isolated SQLite database and verify v1-to-v2 FTS migration, final-only batch writes, list/search pagination, all three export bodies, and retention cleanup. The frontend tests verify the exact list/search URLs, page boundaries, all three download responses, and the cleanup request.

## Manual application flow

1. Start the app, translate a sentence containing a distinctive phrase such as `durable history marker`, and wait for the sentence to become final.
2. Stop translation. Open **Lịch sử**; the finished conversation should appear without reloading the page. Close and reopen the app and confirm it remains present, proving the UI is reading SQLite rather than browser localStorage.
3. Click **Tải thêm** when more than ten conversations exist. Confirm each click appends at most ten more rows and does not reload the full history.
4. Enter `durable history marker` in the search box and click **Tìm**. Confirm the just-finished conversation is returned through `/api/conversations/search`.
5. Click **Xem** and confirm only final sentences are restored into the transcript columns.
6. Click **TXT**, **SRT**, and **JSON**. Open each downloaded file and confirm:
   - TXT contains original and translated text.
   - SRT contains numbered cues, valid timestamps, original text, and translated text.
   - JSON contains the conversation metadata and segments with `is_final: 1`.
7. Set **Giữ lịch sử** to the desired number of days and click **Dọn ngay**. Confirm the dialog, deleted-count status, refreshed history, and storage statistics.

Browser DevTools should show paginated requests with `limit=11&offset=...`; the UI displays ten rows and uses the extra row only to decide whether **Tải thêm** is available.
