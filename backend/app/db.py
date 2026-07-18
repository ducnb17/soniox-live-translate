"""SQLite persistence for conversations, segments, and connection events.

Uses aiosqlite for async access. Database stored at
%APPDATA%/SonioxLiveTranslate/soniox_translate.db on Windows.
"""

import os
import sys
import time
from pathlib import Path

import aiosqlite

from .logging_config import get_logger

log = get_logger("db")

DB_FILENAME = "soniox_translate.db"
SCHEMA_VERSION = 1


def _data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "SonioxLiveTranslate"


def db_path() -> Path:
    return _data_dir() / DB_FILENAME


_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(str(path))
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db() -> None:
    db = await get_db()

    await db.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
    """)
    cursor = await db.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = await cursor.fetchone()
    current_version = row[0] if row else 0

    if current_version < 1:
        await _create_v1(db)
        await db.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (1)")
        await db.commit()
        log.info("db_migrated", version=1)

    if current_version < SCHEMA_VERSION:
        log.warning("db_schema_outdated", current=current_version, latest=SCHEMA_VERSION)


async def _create_v1(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            mode TEXT NOT NULL,
            source_lang TEXT,
            target_lang TEXT NOT NULL,
            input_device TEXT,
            output_device TEXT,
            tts_provider TEXT,
            tts_voice TEXT,
            title TEXT
        );

        CREATE TABLE IF NOT EXISTS connection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            soniox_session_id TEXT,
            event_type TEXT NOT NULL,
            close_code INTEGER,
            close_reason TEXT,
            occurred_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            speaker_label TEXT,
            source_lang TEXT,
            original_text TEXT NOT NULL,
            translated_text TEXT,
            started_at_ms INTEGER,
            ended_at_ms INTEGER,
            is_final INTEGER DEFAULT 0,
            audio_clip_path TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_segments_conversation
            ON segments(conversation_id, started_at_ms);

        CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
            original_text, translated_text, content='segments', content_rowid='id'
        );
    """)


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ── Conversations ──

async def create_conversation(
    id: str,
    started_at: int,
    mode: str,
    target_lang: str,
    source_lang: str | None = None,
    input_device: str | None = None,
    output_device: str | None = None,
    tts_provider: str | None = None,
    tts_voice: str | None = None,
    title: str | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO conversations
           (id, started_at, mode, source_lang, target_lang,
            input_device, output_device, tts_provider, tts_voice, title)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (id, started_at, mode, source_lang, target_lang,
         input_device, output_device, tts_provider, tts_voice, title),
    )
    await db.commit()


async def update_conversation(
    id: str,
    ended_at: int | None = None,
    title: str | None = None,
) -> None:
    db = await get_db()
    sets = []
    params = []
    if ended_at is not None:
        sets.append("ended_at = ?")
        params.append(ended_at)
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if not sets:
        return
    params.append(id)
    await db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", params)
    await db.commit()


async def get_conversation(id: str) -> dict | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM conversations WHERE id = ?", (id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    segments = await get_segments(id)
    events = await get_connection_events(id)
    return {**dict(row), "segments": segments, "connection_events": events}


async def list_conversations(limit: int = 50, offset: int = 0) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM conversations ORDER BY started_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_conversation(id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM segments WHERE conversation_id = ?", (id,))
    await db.execute("DELETE FROM connection_events WHERE conversation_id = ?", (id,))
    await db.execute("DELETE FROM conversations WHERE id = ?", (id,))
    await db.commit()


async def search_conversations(query: str, limit: int = 50) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT DISTINCT c.* FROM conversations c
           JOIN segments s ON s.conversation_id = c.id
           JOIN segments_fts fts ON fts.rowid = s.id
           WHERE segments_fts MATCH ?
           ORDER BY c.started_at DESC LIMIT ?""",
        (query, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ── Connection Events ──

async def add_connection_event(
    conversation_id: str,
    soniox_session_id: str | None,
    event_type: str,
    close_code: int | None = None,
    close_reason: str | None = None,
    occurred_at: int | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO connection_events
           (conversation_id, soniox_session_id, event_type, close_code, close_reason, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (conversation_id, soniox_session_id, event_type, close_code, close_reason,
         occurred_at or int(time.time() * 1000)),
    )
    await db.commit()


async def get_connection_events(conversation_id: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM connection_events WHERE conversation_id = ? ORDER BY occurred_at",
        (conversation_id,),
    )
    return [dict(r) for r in await cursor.fetchall()]


# ── Segments ──

async def add_segment(
    conversation_id: str,
    original_text: str,
    translated_text: str | None = None,
    speaker_label: str | None = None,
    source_lang: str | None = None,
    started_at_ms: int | None = None,
    ended_at_ms: int | None = None,
    is_final: bool = False,
    audio_clip_path: str | None = None,
) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO segments
           (conversation_id, speaker_label, source_lang, original_text,
            translated_text, started_at_ms, ended_at_ms, is_final, audio_clip_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (conversation_id, speaker_label, source_lang, original_text,
         translated_text, started_at_ms, ended_at_ms, 1 if is_final else 0, audio_clip_path),
    )
    await db.commit()
    return cursor.lastrowid


async def add_segments_batch(segments: list[dict]) -> None:
    db = await get_db()
    await db.execute("BEGIN")
    for seg in segments:
        await db.execute(
            """INSERT INTO segments
               (conversation_id, speaker_label, source_lang, original_text,
                translated_text, started_at_ms, ended_at_ms, is_final, audio_clip_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                seg["conversation_id"],
                seg.get("speaker_label"),
                seg.get("source_lang"),
                seg["original_text"],
                seg.get("translated_text"),
                seg.get("started_at_ms"),
                seg.get("ended_at_ms"),
                1 if seg.get("is_final") else 0,
                seg.get("audio_clip_path"),
            ),
        )
    await db.commit()


async def get_segments(conversation_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM segments WHERE conversation_id = ? ORDER BY started_at_ms LIMIT ? OFFSET ?",
        (conversation_id, limit, offset),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def count_segments(conversation_id: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM segments WHERE conversation_id = ?", (conversation_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


# ── Export ──

async def export_conversation_txt(conversation_id: str) -> str:
    conv = await get_conversation(conversation_id)
    if not conv:
        return ""
    lines = [f"=== Conversation: {conversation_id} ==="]
    lines.append(f"Mode: {conv.get('mode')} | Target: {conv.get('target_lang')}")
    lines.append(f"Started: {conv.get('started_at')} | Ended: {conv.get('ended_at', 'N/A')}")
    lines.append("")
    for seg in conv.get("segments", []):
        speaker = f"[Speaker {seg['speaker_label']}] " if seg.get("speaker_label") else ""
        lang = f"({seg.get('source_lang', '')}) " if seg.get("source_lang") else ""
        lines.append(f"{speaker}{lang}Original: {seg['original_text']}")
        if seg.get("translated_text"):
            lines.append(f"{' ' * len(speaker+lang)}Translated: {seg['translated_text']}")
        lines.append("")
    return "\n".join(lines)


async def export_conversation_srt(conversation_id: str) -> str:
    conv = await get_conversation(conversation_id)
    if not conv:
        return ""
    blocks = []
    for i, seg in enumerate(conv.get("segments", []), 1):
        start = seg.get("started_at_ms", 0) or 0
        end = seg.get("ended_at_ms", start + 2000) or start + 2000
        text = seg["original_text"]
        if seg.get("translated_text"):
            text += f"\n[{seg['translated_text']}]"
        blocks.append(
            f"{i}\n"
            f"{_ms_to_srt(start)} --> {_ms_to_srt(end)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


async def export_conversation_json(conversation_id: str) -> str:
    import json
    conv = await get_conversation(conversation_id)
    if not conv:
        return "{}"
    return json.dumps(conv, ensure_ascii=False, indent=2, default=str)


def _ms_to_srt(ms: int) -> str:
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


# ── Retention ──

async def cleanup_old_conversations(max_age_days: int = 30) -> int:
    db = await get_db()
    cutoff = int((time.time() - max_age_days * 86400) * 1000)
    cursor = await db.execute(
        "SELECT id FROM conversations WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,),
    )
    ids = [row[0] for row in await cursor.fetchall()]
    for cid in ids:
        await db.execute("DELETE FROM segments WHERE conversation_id = ?", (cid,))
        await db.execute("DELETE FROM connection_events WHERE conversation_id = ?", (cid,))
        await db.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    await db.commit()
    log.info("retention_cleanup", deleted=len(ids), max_age_days=max_age_days)
    return len(ids)


async def get_db_stats() -> dict:
    db = await get_db()
    cursor = await db.execute("SELECT COUNT(*) FROM conversations")
    conv_count = (await cursor.fetchone())[0]
    cursor = await db.execute("SELECT COUNT(*) FROM segments")
    seg_count = (await cursor.fetchone())[0]
    path = db_path()
    size_mb = path.stat().st_size / (1024 * 1024) if path.exists() else 0
    return {"conversations": conv_count, "segments": seg_count, "db_size_mb": round(size_mb, 2)}
