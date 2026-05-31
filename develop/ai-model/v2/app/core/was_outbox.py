"""Durable retry outbox for WAS writes that failed after a chat response."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiosqlite

from app.core.exceptions import ExternalServiceError
from app.graph.deps import NodeDeps
from app.schemas.state import PendingWrite

logger = logging.getLogger(__name__)


async def ensure_was_outbox_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS was_outbox ("
            "  write_id TEXT PRIMARY KEY,"
            "  user_id TEXT NOT NULL,"
            "  session_id TEXT,"
            "  trace_id TEXT,"
            "  write_type TEXT NOT NULL,"
            "  idempotency_key TEXT,"
            "  payload_json TEXT NOT NULL,"
            "  status TEXT NOT NULL DEFAULT 'pending',"
            "  attempt_count INTEGER NOT NULL DEFAULT 0,"
            "  next_attempt_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  last_error TEXT,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_was_outbox_due "
            "ON was_outbox(status, next_attempt_at)"
        )
        await db.commit()


async def enqueue_was_outbox(
    db_path: str,
    *,
    user_id: str,
    session_id: str | None,
    trace_id: str | None,
    writes: list[PendingWrite],
) -> None:
    if not writes:
        return
    await ensure_was_outbox_table(db_path)
    async with aiosqlite.connect(db_path) as db:
        for write in writes:
            write_id = str(write.get("write_id") or write.get("idempotency_key") or "")
            if not write_id:
                continue
            payload = write.get("payload") or {}
            await db.execute(
                "INSERT INTO was_outbox ("
                "  write_id, user_id, session_id, trace_id, write_type, idempotency_key, payload_json, status,"
                "  next_attempt_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'), datetime('now')) "
                "ON CONFLICT(write_id) DO UPDATE SET "
                "  status = CASE WHEN was_outbox.status = 'succeeded' THEN was_outbox.status ELSE 'pending' END,"
                "  session_id = excluded.session_id,"
                "  trace_id = excluded.trace_id,"
                "  payload_json = excluded.payload_json,"
                "  updated_at = datetime('now')",
                (
                    write_id,
                    user_id,
                    session_id,
                    trace_id,
                    write["write_type"],
                    str(write.get("idempotency_key") or write_id),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )
        await db.commit()


async def mark_was_outbox_succeeded(db_path: str, write_id: str | None) -> None:
    if not write_id:
        return
    await ensure_was_outbox_table(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE was_outbox SET status = 'succeeded', updated_at = datetime('now'), last_error = NULL "
            "WHERE write_id = ?",
            (write_id,),
        )
        await db.commit()


async def replay_due_was_outbox(
    db_path: str,
    deps: NodeDeps,
    *,
    limit: int = 20,
) -> dict[str, int]:
    await ensure_was_outbox_table(db_path)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT write_id, user_id, write_type, payload_json, attempt_count "
            "FROM was_outbox "
            "WHERE status = 'pending' AND next_attempt_at <= datetime('now') "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()

    succeeded = 0
    failed = 0
    for write_id, user_id, write_type, payload_json, attempt_count in rows:
        try:
            payload = json.loads(payload_json)
            await execute_outbox_write(deps, user_id, {"write_type": write_type, "payload": payload})
            await mark_was_outbox_succeeded(db_path, write_id)
            succeeded += 1
        except Exception as exc:
            failed += 1
            await _mark_was_outbox_failed(db_path, write_id, attempt_count, exc)
    return {"attempted": len(rows), "succeeded": succeeded, "failed": failed}


async def periodic_was_outbox_replay(
    db_path: str,
    deps: NodeDeps,
    *,
    interval_seconds: int = 60,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await replay_due_was_outbox(db_path, deps)
            if result["attempted"]:
                logger.info("WAS outbox replay result: %s", result)
        except Exception as exc:
            logger.warning("WAS outbox periodic replay failed: %s", exc)


async def execute_outbox_write(deps: NodeDeps, user_id: str, write: dict[str, Any]) -> None:
    write_type = write["write_type"]
    payload = write["payload"]
    idempotency_key = payload.get("_idempotency_key") or payload.get("idempotency_key")
    if write_type == "profile":
        await deps.was.put_user_profile(user_id, payload)
    elif write_type == "plan_check":
        await deps.was.put_plan_check(user_id, payload["item_id"], idempotency_key=idempotency_key)
    elif write_type == "plan_create":
        await deps.was.post_plan_create(user_id, payload)
    elif write_type == "plan_update":
        await deps.was.put_plan_update(user_id, payload)
    elif write_type == "plan_delete":
        await deps.was.delete_plan(user_id, payload)
    else:
        raise ExternalServiceError(service="WAS", message=f"Unknown outbox write_type: {write_type}")


async def _mark_was_outbox_failed(
    db_path: str,
    write_id: str,
    attempt_count: int,
    exc: Exception,
) -> None:
    delay_minutes = min(60, max(1, 2 ** int(attempt_count or 0)))
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE was_outbox SET "
            "  attempt_count = attempt_count + 1,"
            "  last_error = ?,"
            "  next_attempt_at = datetime('now', ?),"
            "  updated_at = datetime('now') "
            "WHERE write_id = ?",
            (str(exc), f"+{delay_minutes} minutes", write_id),
        )
        await db.commit()
