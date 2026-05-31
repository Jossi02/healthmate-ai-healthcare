"""SQLite-backed session locks for cross-process chat serialization."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class SessionLockHandle:
    db_path: str
    session_id: str
    owner: str

    async def release(self) -> None:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM session_locks WHERE session_id = ? AND owner = ?",
                    (self.session_id, self.owner),
                )
                await db.commit()
        except Exception as exc:
            logger.warning("Failed to release session lock: session=%s error=%s", self.session_id, exc)


async def ensure_session_lock_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS session_locks ("
            "  session_id TEXT PRIMARY KEY,"
            "  owner TEXT NOT NULL,"
            "  acquired_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await db.commit()


async def acquire_session_lock(
    db_path: str,
    session_id: str,
    *,
    ttl_seconds: int = 120,
    wait_timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.08,
) -> SessionLockHandle:
    owner = uuid.uuid4().hex
    deadline = time.monotonic() + wait_timeout_seconds
    await ensure_session_lock_table(db_path)

    while True:
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    "DELETE FROM session_locks "
                    "WHERE session_id = ? AND acquired_at < datetime('now', ?)",
                    (session_id, f"-{ttl_seconds} seconds"),
                )
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO session_locks (session_id, owner, acquired_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (session_id, owner),
                )
                await db.commit()
                if cursor.rowcount == 1:
                    return SessionLockHandle(db_path=db_path, session_id=session_id, owner=owner)
        except Exception as exc:
            logger.warning("Session lock acquire attempt failed: session=%s error=%s", session_id, exc)

        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for session lock: {session_id}")
        await asyncio.sleep(poll_interval_seconds)
