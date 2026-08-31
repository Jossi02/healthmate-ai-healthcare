from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.checkpoint_filter import FilteringAsyncSqliteSaver
from app.core.lifespan import (
    _cleanup_old_checkpoints,
    _ensure_activity_table,
    _seed_missing_session_activity,
)
from app.core.session_lock import acquire_session_lock, ensure_session_lock_table
from app.core.was_outbox import enqueue_was_outbox, ensure_was_outbox_table


async def _count(
    db_path: str,
    table: str,
    thread_id: str,
    *,
    id_column: str = "thread_id",
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {id_column} = ?",
            (thread_id,),
        )
        row = await cursor.fetchone()
    return int(row[0])


async def _seed_checkpoint(
    db_path: str,
    *,
    thread_id: str,
    checkpoint_id: str,
    task_id: str,
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO checkpoints ("
            "thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata"
            ") VALUES (?, '', ?, NULL, 'test', ?, ?)",
            (thread_id, checkpoint_id, b"checkpoint", b"metadata"),
        )
        await db.execute(
            "INSERT INTO writes ("
            "thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value"
            ") VALUES (?, '', ?, ?, 0, 'today_plan', 'test', ?)",
            (thread_id, checkpoint_id, task_id, b"write"),
        )
        await db.commit()


async def _seed_activity(db_path: str, thread_id: str, expression: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO session_activity (thread_id, last_active) VALUES (?, datetime('now', ?))",
            (thread_id, expression),
        )
        await db.commit()


def test_checkpoint_retention() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "checkpoints.sqlite")

        async def scenario() -> None:
            checkpointer = FilteringAsyncSqliteSaver(await aiosqlite.connect(db_path))
            await checkpointer.setup()
            await checkpointer.conn.close()
            await _ensure_activity_table(db_path)
            await ensure_session_lock_table(db_path)
            await ensure_was_outbox_table(db_path)

            expired = "chat:expired"
            active = "chat:active"
            orphan_checkpoint = "chat:orphan-checkpoint"
            orphan_write = "chat:orphan-write"
            await _seed_checkpoint(
                db_path,
                thread_id=expired,
                checkpoint_id="checkpoint-expired",
                task_id="task-expired",
            )
            await _seed_checkpoint(
                db_path,
                thread_id=active,
                checkpoint_id="checkpoint-active",
                task_id="task-active",
            )
            await _seed_checkpoint(
                db_path,
                thread_id=orphan_checkpoint,
                checkpoint_id="checkpoint-orphan",
                task_id="task-orphan",
            )
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "INSERT INTO writes ("
                    "thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value"
                    ") VALUES (?, '', ?, ?, 0, 'today_plan', 'test', ?)",
                    (orphan_write, "checkpoint-orphan-write", "task-orphan-write", b"write"),
                )
                await db.commit()
            await _seed_activity(db_path, expired, "-73 hours")
            await _seed_activity(db_path, active, "-71 hours")

            await enqueue_was_outbox(
                db_path,
                user_id="user-a",
                session_id="legacy-public-session",
                trace_id="trace-a",
                writes=[
                    {
                        "write_id": "pending-write",
                        "write_type": "profile",
                        "payload": {"nickname": "pending"},
                    }
                ],
            )
            await _seed_missing_session_activity(db_path)
            assert await _count(db_path, "session_activity", orphan_checkpoint) == 1
            assert await _count(db_path, "session_activity", orphan_write) == 1

            lock = await acquire_session_lock(db_path, expired)
            await _cleanup_old_checkpoints(db_path, ttl_hours=72)
            assert await _count(db_path, "checkpoints", expired) == 1
            assert await _count(db_path, "writes", expired) == 1
            assert await _count(db_path, "session_activity", expired) == 1
            assert await _count(db_path, "checkpoints", active) == 1
            assert await _count(db_path, "writes", active) == 1
            assert await _count(db_path, "session_activity", active) == 1
            assert await _count(
                db_path,
                "session_locks",
                expired,
                id_column="session_id",
            ) == 1
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT status, COUNT(*) FROM was_outbox"
                )
                row = await cursor.fetchone()
            assert row == ("pending", 1)

            await lock.release()
            async with aiosqlite.connect(db_path) as db:
                await db.executemany(
                    "INSERT INTO session_activity (thread_id, last_active) "
                    "VALUES (?, datetime('now', '-73 hours'))",
                    [(f"chat:bulk-expired-{index}",) for index in range(1001)],
                )
                await db.commit()
            await _cleanup_old_checkpoints(db_path, ttl_hours=72)
            assert await _count(db_path, "checkpoints", expired) == 0
            assert await _count(db_path, "writes", expired) == 0
            assert await _count(db_path, "session_activity", expired) == 0
            assert await _count(
                db_path,
                "session_locks",
                expired,
                id_column="session_id",
            ) == 0
            assert await _count(db_path, "checkpoints", active) == 1
            assert await _count(db_path, "writes", active) == 1
            assert await _count(db_path, "session_activity", active) == 1
            assert await _count(db_path, "checkpoints", orphan_checkpoint) == 1
            assert await _count(db_path, "writes", orphan_checkpoint) == 1
            assert await _count(db_path, "writes", orphan_write) == 1
            assert await _count(db_path, "session_activity", orphan_checkpoint) == 1
            assert await _count(db_path, "session_activity", orphan_write) == 1
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT status, COUNT(*) FROM was_outbox"
                )
                row = await cursor.fetchone()
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM session_activity "
                    "WHERE thread_id LIKE 'chat:bulk-expired-%'"
                )
                bulk_expired_count = int((await cursor.fetchone())[0])
            assert row == ("pending", 1)
            assert bulk_expired_count == 0

        asyncio.run(scenario())


if __name__ == "__main__":
    test_checkpoint_retention()
    print("checkpoint retention test passed")
