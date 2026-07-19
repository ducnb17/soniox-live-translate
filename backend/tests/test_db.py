import time

import pytest

from app import db as database


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    await database.close_db()
    monkeypatch.setattr(database, "_data_dir", lambda: tmp_path)
    await database.init_db()
    yield
    await database.close_db()


async def create_conversation(conversation_id: str, started_at: int, ended_at: int | None = None) -> None:
    await database.create_conversation(
        id=conversation_id,
        started_at=started_at,
        mode="one_way",
        target_lang="vi",
    )
    if ended_at is not None:
        await database.update_conversation(conversation_id, ended_at=ended_at)


class TestConversationPersistence:
    async def test_v1_migration_rebuilds_fts_for_existing_history(self, tmp_path, monkeypatch):
        await database.close_db()
        monkeypatch.setattr(database, "_data_dir", lambda: tmp_path)
        raw_db = await database.get_db()
        await raw_db.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        await database._create_v1(raw_db)
        await raw_db.execute("INSERT INTO schema_version(version) VALUES (1)")
        await raw_db.execute(
            "INSERT INTO conversations(id, started_at, mode, target_lang) VALUES (?, ?, ?, ?)",
            ("legacy", 1000, "one_way", "vi"),
        )
        await raw_db.execute(
            """INSERT INTO segments(conversation_id, original_text, translated_text, is_final)
               VALUES (?, ?, ?, 1)""",
            ("legacy", "existing searchable sentence", "câu cũ tìm được"),
        )
        await raw_db.commit()
        await database.close_db()

        await database.init_db()
        try:
            assert [row["id"] for row in await database.search_conversations("searchable sentence")] == ["legacy"]
        finally:
            await database.close_db()

    async def test_batch_keeps_only_final_segments_and_fts_finds_both_columns(self, isolated_db):
        await create_conversation("conv-search", 1000)

        inserted = await database.add_segments_batch([
            {
                "conversation_id": "conv-search",
                "original_text": "hello durable history",
                "translated_text": "xin chào lịch sử",
                "started_at_ms": 0,
                "ended_at_ms": 1200,
                "is_final": True,
            },
            {
                "conversation_id": "conv-search",
                "original_text": "partial must not persist",
                "is_final": False,
            },
            {
                "conversation_id": "conv-search",
                "original_text": "second final sentence",
                "translated_text": "câu cuối thứ hai",
                "started_at_ms": 1200,
                "ended_at_ms": 2500,
                "is_final": True,
            },
        ])

        assert inserted == 2
        segments = await database.get_segments("conv-search")
        assert len(segments) == 2
        assert {segment["is_final"] for segment in segments} == {1}
        assert "partial must not persist" not in {segment["original_text"] for segment in segments}
        assert [row["id"] for row in await database.search_conversations("durable history")] == ["conv-search"]
        assert [row["id"] for row in await database.search_conversations("xin chào")] == ["conv-search"]

    async def test_non_final_single_insert_is_rejected(self, isolated_db):
        await create_conversation("conv-final-only", 1000)
        with pytest.raises(ValueError, match="only final"):
            await database.add_segment(
                conversation_id="conv-final-only",
                original_text="interim text",
                is_final=False,
            )
        assert await database.count_segments("conv-final-only") == 0

    async def test_list_and_search_are_paginated(self, isolated_db):
        for index in range(4):
            conversation_id = f"conv-{index}"
            await create_conversation(conversation_id, 1000 + index)
            await database.add_segments_batch([{
                "conversation_id": conversation_id,
                "original_text": f"shared keyword sentence {index}",
                "is_final": True,
            }])

        first_page = await database.list_conversations(limit=2, offset=0)
        second_page = await database.list_conversations(limit=2, offset=2)
        assert [row["id"] for row in first_page] == ["conv-3", "conv-2"]
        assert [row["id"] for row in second_page] == ["conv-1", "conv-0"]
        assert first_page[0]["segment_count"] == 1
        assert first_page[0]["preview"] == "shared keyword sentence 3"

        search_page = await database.search_conversations("shared keyword", limit=2, offset=1)
        assert [row["id"] for row in search_page] == ["conv-2", "conv-1"]

    async def test_exports_contain_saved_final_content(self, isolated_db):
        await create_conversation("conv-export", 1000, ended_at=3000)
        await database.add_segments_batch([{
            "conversation_id": "conv-export",
            "original_text": "Hello database",
            "translated_text": "Xin chào cơ sở dữ liệu",
            "speaker_label": "1",
            "source_lang": "en",
            "started_at_ms": 100,
            "ended_at_ms": 2100,
            "is_final": True,
        }])

        txt = await database.export_conversation_txt("conv-export")
        srt = await database.export_conversation_srt("conv-export")
        exported_json = await database.export_conversation_json("conv-export")
        assert "Hello database" in txt and "Xin chào cơ sở dữ liệu" in txt
        assert "00:00:00,100 --> 00:00:02,100" in srt
        assert "Hello database" in srt and "Xin chào cơ sở dữ liệu" in srt
        assert '"is_final": 1' in exported_json and "Hello database" in exported_json

    async def test_manual_retention_cleanup_removes_old_conversation_and_fts_rows(self, isolated_db):
        now_ms = int(time.time() * 1000)
        await create_conversation("old", now_ms - 40 * 86400 * 1000, ended_at=now_ms - 35 * 86400 * 1000)
        await create_conversation("recent", now_ms - 2 * 86400 * 1000, ended_at=now_ms - 86400 * 1000)
        for conversation_id in ("old", "recent"):
            await database.add_segments_batch([{
                "conversation_id": conversation_id,
                "original_text": f"retention marker {conversation_id}",
                "is_final": True,
            }])

        assert await database.cleanup_old_conversations(max_age_days=30) == 1
        assert await database.get_conversation("old") is None
        assert await database.get_conversation("recent") is not None
        assert await database.search_conversations("retention marker old") == []
