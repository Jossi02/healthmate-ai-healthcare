"""FastAPI lifespan setup for clients, graph, and background services."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

import aiosqlite
import httpx
from fastapi import FastAPI
from pinecone import PineconeAsyncio, ServerlessSpec

from app.clients.embedding import EMBEDDING_DIM, EmbeddingClient
from app.clients.gemini import GeminiClient
from app.clients.pinecone import PineconeClient
from app.clients.was import WASClient
from app.core.checkpoint_filter import FilteringAsyncSqliteSaver
from app.core.config import get_settings
from app.core.profile_sync import ProfileSyncTracker
from app.core.session_lock import ensure_session_lock_table
from app.core.trace_store import TraceLogHandler, TraceStore
from app.core.was_outbox import ensure_was_outbox_table, periodic_was_outbox_replay
from app.graph.builder import build_graph
from app.graph.deps import NodeDeps
from app.services.langsmith_quality import LangSmithQualityExporter

logger = logging.getLogger(__name__)


async def _ensure_activity_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS session_activity ("
            "  thread_id TEXT PRIMARY KEY,"
            "  last_active TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await db.commit()


async def update_session_activity(db_path: str, thread_id: str) -> None:
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO session_activity (thread_id, last_active) VALUES (?, datetime('now')) "
                "ON CONFLICT(thread_id) DO UPDATE SET last_active = datetime('now')",
                (thread_id,),
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Failed to update session activity: %s", exc)


async def _cleanup_old_checkpoints(db_path: str, ttl_hours: int) -> None:
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT thread_id FROM session_activity "
                "WHERE last_active < datetime('now', ?)",
                (f"-{ttl_hours} hours",),
            )
            expired = [row[0] for row in await cursor.fetchall()]

            if not expired:
                logger.info("No expired sessions to clean up")
                return

            placeholders = ",".join("?" for _ in expired)
            for table in ("checkpoints", "writes"):
                await db.execute(
                    f"DELETE FROM {table} WHERE thread_id IN ({placeholders})",
                    expired,
                )
            await db.execute(
                f"DELETE FROM session_activity WHERE thread_id IN ({placeholders})",
                expired,
            )
            await db.commit()
            await db.execute("VACUUM")
            logger.info("Cleaned up %d expired sessions", len(expired))
    except Exception as exc:
        logger.warning("Checkpoint cleanup failed: %s", exc)


async def _periodic_cleanup(db_path: str, ttl_hours: int, interval: int = 3600) -> None:
    while True:
        await asyncio.sleep(interval)
        await _cleanup_old_checkpoints(db_path, ttl_hours)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    embed_client = None
    pinecone_client = None
    app.state._pinecone_control = None
    if settings.ENABLE_RAG_MEMORY and settings.PINECONE_API_KEY:
        embed_client = EmbeddingClient(api_key=settings.GEMINI_API_KEY)
        logger.info("EmbeddingClient initialized (dim=%d)", EMBEDDING_DIM)

        pc = PineconeAsyncio(api_key=settings.PINECONE_API_KEY)
        app.state._pinecone_control = pc

        if not await pc.has_index(settings.PINECONE_INDEX_NAME):
            await pc.create_index(
                name=settings.PINECONE_INDEX_NAME,
                dimension=EMBEDDING_DIM,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )

        description = await pc.describe_index(settings.PINECONE_INDEX_NAME)
        index = pc.IndexAsyncio(host=description.host)
        pinecone_client = PineconeClient(index)
        logger.info("Pinecone initialized (index=%s)", settings.PINECONE_INDEX_NAME)
    else:
        logger.info("Pinecone/RAG memory disabled for fast demo flow")

    gemini_client = GeminiClient(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.GEMINI_MODEL_NAME,
    )
    logger.info("GeminiClient initialized (model=%s)", settings.GEMINI_MODEL_NAME)

    router_api_key = settings.ROUTER_API_KEY or settings.GEMINI_API_KEY
    router_client = GeminiClient(
        api_key=router_api_key,
        model_name=settings.ROUTER_MODEL_NAME,
    )
    logger.info("RouterClient initialized (model=%s)", settings.ROUTER_MODEL_NAME)

    trace_store = TraceStore()
    trace_handler = TraceLogHandler(trace_store)
    logging.getLogger().addHandler(trace_handler)
    app.state.trace_store = trace_store
    app.state._trace_log_handler = trace_handler
    logger.info("TraceStore initialized")

    langsmith_quality = LangSmithQualityExporter.from_settings(settings)
    app.state.langsmith_quality = langsmith_quality
    logger.info(
        "LangSmith quality export initialized (enabled=%s, configured=%s, project=%s)",
        langsmith_quality.enabled,
        langsmith_quality.configured,
        langsmith_quality.project_name,
    )

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.WAS_TIMEOUT))
    was_client = WASClient(
        base_url=settings.WAS_BASE_URL,
        client=http_client,
        api_key=settings.INTERNAL_API_KEY,
        trace_store=trace_store,
    )
    app.state._was_http_client = http_client
    logger.info("WASClient initialized (base_url=%s)", settings.WAS_BASE_URL)

    profile_sync = ProfileSyncTracker()
    app.state.profile_sync = profile_sync
    logger.info("ProfileSyncTracker initialized")

    db_path = settings.CHECKPOINT_DB_PATH
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    app.state.checkpoint_db_path = db_path
    checkpointer_conn = await aiosqlite.connect(db_path)
    checkpointer = FilteringAsyncSqliteSaver(checkpointer_conn)
    await checkpointer.setup()
    app.state._checkpointer = checkpointer
    logger.info("SQLite checkpointer initialized (path=%s)", db_path)

    deps = NodeDeps(
        gemini=gemini_client,
        router=router_client,
        was=was_client,
        pinecone=pinecone_client,
        embed=embed_client,
        profile_sync=profile_sync,
        trace=trace_store,
    )
    app.state.graph = build_graph(deps, checkpointer=checkpointer)
    app.state.deps = deps
    logger.info("LangGraph compiled and ready")

    await _ensure_activity_table(db_path)
    await ensure_session_lock_table(db_path)
    await ensure_was_outbox_table(db_path)
    cleanup_task = asyncio.create_task(
        _periodic_cleanup(db_path, settings.CHECKPOINT_TTL_HOURS)
    )
    outbox_task = asyncio.create_task(
        periodic_was_outbox_replay(db_path, deps, interval_seconds=60)
    )
    await _cleanup_old_checkpoints(db_path, settings.CHECKPOINT_TTL_HOURS)

    yield

    cleanup_task.cancel()
    outbox_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
    with suppress(asyncio.CancelledError):
        await outbox_task
    try:
        await app.state._checkpointer.conn.close()
    except Exception as exc:
        logger.warning("Failed to close checkpointer: %s", exc)
    logging.getLogger().removeHandler(app.state._trace_log_handler)
    if app.state._pinecone_control is not None:
        await app.state._pinecone_control.close()
    await app.state._was_http_client.aclose()
    logger.info("Shutdown complete")
