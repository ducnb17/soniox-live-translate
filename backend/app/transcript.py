"""Per-session transcript persistence.

Each active WebSocket session owns a `TranscriptSession` whose `utterances`
list is appended to by the browser (via a control message) and persisted to
disk when the session ends. `GET /transcript/{session_id}` returns the JSON.
"""

import json
import time
import uuid
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException

from .config import TRANSCRIPT_DIR


class TranscriptStore:
    """In-memory registry of live sessions + on-disk JSON files."""

    def __init__(self) -> None:
        self.sessions: dict[str, TranscriptSession] = {}

    def new(self) -> "TranscriptSession":
        session = TranscriptSession()
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> "TranscriptSession":
        session = self.sessions.get(session_id)
        if session is None:
            path = TRANSCRIPT_DIR / f"{session_id}.json"
            if not path.exists():
                raise HTTPException(status_code=404, detail="transcript not found")
            data = json.loads(path.read_text())
            # Handle both the full payload shape and a bare utterances list.
            utterances = data.get("utterances", data) if isinstance(data, dict) else data
            meta = data.get("meta", {}) if isinstance(data, dict) else {}
            return TranscriptSession(
                id=session_id, utterances=utterances, meta=meta
            )
        return session
        return session

    def finish(self, session: "TranscriptSession") -> None:
        """Persist to disk and drop from the live registry."""
        path = TRANSCRIPT_DIR / f"{session.id}.json"
        path.write_text(json.dumps(session.payload(), ensure_ascii=False, indent=2))
        self.sessions.pop(session.id, None)


class TranscriptSession:
    def __init__(
        self,
        id: str | None = None,
        utterances: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.id = id or uuid.uuid4().hex
        self.utterances = list(utterances or [])
        self.meta: dict[str, Any] = meta or {
            "started_at": time.time(),
            "ended_at": None,
        }

    def add(self, utterance: dict[str, Any]) -> None:
        self.utterances.append(utterance)

    def add_many(self, utterances: Iterable[dict[str, Any]]) -> None:
        self.utterances.extend(utterances)

    def close(self) -> None:
        self.meta["ended_at"] = time.time()

    def payload(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "meta": self.meta,
            "utterances": self.utterances,
        }