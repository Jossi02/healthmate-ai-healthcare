"""Preprocess node for session hydration and profile refresh."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.core.conversation_state import (
    empty_context_resolution,
    merge_profile_override_for_plan_context,
    profile_context_changed,
    profile_context_changed_fields,
)
from app.core.exceptions import ExternalServiceError
from app.core.was_outbox import execute_outbox_write, mark_was_outbox_succeeded
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)

_MAX_PENDING_WRITES = 24
_MAX_SESSION_REPLAY_ATTEMPTS = 4


def make_preprocess_node(deps: NodeDeps):
    async def preprocess_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        deps.trace.record_current_event(
            stage="preprocess",
            status="info",
            title="Preprocess started",
            detail={
                "is_session_start": bool(state.get("is_session_start", True)),
                "pending_writes": len(_safe_list(state.get("pending_writes"))),
            },
        )
        updates: dict = {
            "action_intent": None,
            "domain": "general",
            "support_mode": "normal",
            "ambiguous": False,
            "routing_diagnostics": None,
            "context_resolution": empty_context_resolution(),
            "search_results": [],
            "search_quality": "ok",
            "search_retry_count": 0,
            "search_query": None,
            "profile_changes": None,
            "modify_plan_context": None,
            "profile_constraints": None,
            "retrieval_decision": None,
            "draft_response": None,
            "draft_components": None,
            "response": None,
            "force_regenerate": False,
            "validation_report": None,
            "validation_retry_count": 0,
            "self_eval_count": 0,
            "self_eval_failure_reason": None,
            "needs_clarification": False,
        }

        current_turn = _safe_pending_int(state.get("turn_count"), default=0, minimum=0) + 1
        pending = _normalize_pending_writes(state.get("pending_writes", []), current_turn=current_turn)
        still_pending = []
        replayed_profile_changes: dict = {}
        pending_profile_overlay: dict = {}
        replayed_plan_write = False
        for write in pending:
            if _pending_write_waiting_for_retry(write, current_turn):
                still_pending.append(write)
                if write.get("write_type") == "profile" and isinstance(write.get("payload"), dict):
                    pending_profile_overlay.update(write["payload"])
                continue
            if _pending_write_exhausted(write):
                deps.trace.record_current_alert(
                    severity="warning",
                    message="Pending write moved to durable outbox after repeated session replay failures",
                    detail={
                        "write_type": write.get("write_type"),
                        "write_id": write.get("write_id"),
                        "attempt_count": write.get("attempt_count"),
                    },
                )
                continue
            try:
                await _execute_write(deps, state["user_id"], write)
                db_path = state.get("checkpoint_db_path")
                if db_path:
                    await mark_was_outbox_succeeded(str(db_path), write.get("write_id"))
                if write.get("write_type") == "profile" and isinstance(write.get("payload"), dict):
                    replayed_profile_changes.update(_strip_write_metadata(write["payload"]))
                if write.get("write_type") in {"plan_check", "plan_create", "plan_update", "plan_delete"}:
                    replayed_plan_write = True
                logger.info("Replayed pending write: %s", write["write_type"])
                deps.trace.record_current_event(
                    stage="preprocess",
                    status="ok",
                    title="Pending write replayed",
                    detail={"write_type": write["write_type"]},
                )
            except ExternalServiceError as exc:
                failed_write = _mark_pending_write_failed(write, current_turn, exc)
                if not _pending_write_exhausted(failed_write):
                    still_pending.append(failed_write)
                if write.get("write_type") == "profile" and isinstance(write.get("payload"), dict):
                    pending_profile_overlay.update(write["payload"])
                logger.warning("Pending write still failing: %s", write["write_type"])
                deps.trace.record_current_alert(
                    severity="warning",
                    message="Pending write replay still failing",
                    detail={
                        "write_type": write["write_type"],
                        "attempt_count": failed_write.get("attempt_count"),
                        "next_retry_turn": failed_write.get("next_retry_turn"),
                    },
                )
            except Exception as exc:
                failed_write = _mark_pending_write_failed(write, current_turn, exc)
                if not _pending_write_exhausted(failed_write):
                    still_pending.append(failed_write)
                if write.get("write_type") == "profile" and isinstance(write.get("payload"), dict):
                    pending_profile_overlay.update(write["payload"])
                logger.warning(
                    "Pending write replay failed with unexpected error: %s (%s)",
                    write["write_type"],
                    exc,
                )
                deps.trace.record_current_alert(
                    severity="error",
                    message="Pending write replay raised unexpected error",
                    detail={
                        "write_type": write["write_type"],
                        "error": str(exc),
                        "attempt_count": failed_write.get("attempt_count"),
                        "next_retry_turn": failed_write.get("next_retry_turn"),
                    },
                )
        updates["pending_writes"] = _cap_pending_writes(still_pending)
        if replayed_profile_changes:
            await _mark_profile_updated(deps, state["user_id"])

        user_id = state["user_id"]
        current_profile_version = await deps.profile_sync.get_profile_version(user_id)
        state_profile_version = _safe_pending_int(state.get("profile_sync_version"), default=0, minimum=0)
        is_session_start = bool(state.get("is_session_start", True))
        should_refresh_profile = current_profile_version > state_profile_version

        if is_session_start:
            profile_override = _safe_dict(state.get("user_profile")) if state.get("profile_override_applied") else None
            profile, profile_loaded = await _load_user_profile_with_fallback(
                deps=deps,
                user_id=user_id,
                fallback=state.get("user_profile"),
                context="initial",
            )
            if profile_override:
                profile = _normalize_user_profile(merge_profile_override_for_plan_context(profile, profile_override))
            if replayed_profile_changes:
                profile = _normalize_user_profile(merge_profile_override_for_plan_context(profile, replayed_profile_changes))
            today_plan, today_plan_loaded = await _load_today_plan_with_fallback(
                deps=deps,
                user_id=user_id,
                fallback=state.get("today_plan"),
                context="initial",
            )

            updates["user_profile"] = profile
            updates["today_plan"] = today_plan
            updates["profile_sync_version"] = (
                current_profile_version if profile_loaded or replayed_profile_changes else state_profile_version
            )
            if _should_clear_active_proposal_for_profile_change(state, profile):
                updates.update(_clear_active_proposal_updates())
                deps.trace.record_current_event(
                    stage="state.active_proposal",
                    status="ok",
                    title="Active proposal invalidated by initial profile hydration",
                    detail={"source": "initial_hydration", "changed_fields": _profile_context_changed_fields(state.get("user_profile"), profile)},
                )

            deps.trace.record_current_event(
                stage="preprocess",
                status="ok" if profile_loaded or today_plan_loaded else "warn",
                title="Initial WAS hydration completed",
                detail={
                    "profile_loaded": profile_loaded,
                    "today_plan_loaded": today_plan_loaded,
                    "profile_sync_version": updates["profile_sync_version"],
                    "today_plan_items": len(today_plan or []),
                },
            )
            updates["is_session_start"] = False
        elif should_refresh_profile:
            profile, profile_loaded = await _load_user_profile_with_fallback(
                deps=deps,
                user_id=user_id,
                fallback=state.get("user_profile"),
                context="refresh",
            )
            if replayed_profile_changes:
                profile = _normalize_user_profile(merge_profile_override_for_plan_context(profile, replayed_profile_changes))
            updates["user_profile"] = profile
            updates["profile_sync_version"] = (
                current_profile_version if profile_loaded or replayed_profile_changes else state_profile_version
            )
            if _should_clear_active_proposal_for_profile_change(state, profile):
                updates.update(_clear_active_proposal_updates())
                deps.trace.record_current_event(
                    stage="state.active_proposal",
                    status="ok",
                    title="Active proposal invalidated by refreshed profile",
                    detail={"source": "was_refresh", "changed_fields": _profile_context_changed_fields(state.get("user_profile"), profile)},
                )
            deps.trace.record_current_event(
                stage="preprocess",
                status="ok" if profile_loaded else "warn",
                title="Profile refresh from WAS completed",
                detail={
                    "profile_loaded": profile_loaded,
                    "profile_sync_version": updates["profile_sync_version"],
                },
            )

        elif replayed_profile_changes:
            profile = _normalize_user_profile(
                merge_profile_override_for_plan_context(_safe_dict(state.get("user_profile")), replayed_profile_changes)
            )
            updates["user_profile"] = profile
            updates["profile_sync_version"] = current_profile_version or state_profile_version
            if _should_clear_active_proposal_for_profile_change(state, profile):
                updates.update(_clear_active_proposal_updates())
                deps.trace.record_current_event(
                    stage="state.active_proposal",
                    status="ok",
                    title="Active proposal invalidated by replayed profile changes",
                    detail={"source": "pending_profile_replay", "changed_fields": _profile_context_changed_fields(state.get("user_profile"), profile)},
                )

        if replayed_plan_write and not is_session_start:
            today_plan, today_plan_loaded = await _load_today_plan_with_fallback(
                deps=deps,
                user_id=user_id,
                fallback=state.get("today_plan"),
                context="pending_replay",
            )
            updates["today_plan"] = today_plan
            deps.trace.record_current_event(
                stage="preprocess",
                status="ok" if today_plan_loaded else "warn",
                title="Today plan refreshed after pending write replay",
                detail={
                    "today_plan_loaded": today_plan_loaded,
                    "today_plan_items": len(today_plan or []),
                },
            )

        base_profile = _normalize_user_profile(updates.get("user_profile") or state.get("user_profile"))
        if pending_profile_overlay:
            updates["pending_profile_overlay"] = pending_profile_overlay
            updates["effective_user_profile"] = _normalize_user_profile(
                merge_profile_override_for_plan_context(base_profile, pending_profile_overlay)
            )
        elif not any(write.get("write_type") == "profile" for write in still_pending):
            updates["pending_profile_overlay"] = None
            updates["effective_user_profile"] = None

        updates["turn_count"] = current_turn
        deps.trace.record_current_event(
            stage="preprocess",
            status="ok",
            title="Preprocess completed",
            detail={"turn_count": updates["turn_count"]},
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return updates

    return preprocess_node


async def _mark_profile_updated(deps: NodeDeps, user_id: str) -> None:
    marker = getattr(deps.profile_sync, "mark_profile_updated", None)
    if marker:
        await marker(user_id)


async def _execute_write(deps: NodeDeps, user_id: str, write: dict) -> None:
    await execute_outbox_write(deps, user_id, write)


def _normalize_pending_writes(writes: object, *, current_turn: int) -> list[dict[str, Any]]:
    if not isinstance(writes, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for write in writes:
        if not isinstance(write, dict):
            continue
        key = _pending_write_key(write)
        if not key or key in seen:
            continue
        seen.add(key)
        next_write = dict(write)
        if not isinstance(next_write.get("payload"), dict):
            continue
        next_write.setdefault("write_id", key)
        next_write.setdefault("idempotency_key", key)
        next_write["attempt_count"] = _safe_pending_int(next_write.get("attempt_count"), default=0, minimum=0)
        next_write["created_turn"] = _safe_pending_int(next_write.get("created_turn"), default=current_turn, minimum=0)
        next_write["next_retry_turn"] = _safe_pending_int(next_write.get("next_retry_turn"), default=current_turn, minimum=current_turn)
        if "last_error" in next_write:
            next_write["last_error"] = str(next_write.get("last_error") or "")[:500]
        normalized.append(next_write)
    return _cap_pending_writes(normalized)


def _pending_write_key(write: dict[str, Any]) -> str:
    write_type = str(write.get("write_type") or "").strip()
    if not write_type:
        return ""
    explicit = str(write.get("write_id") or write.get("idempotency_key") or "").strip()
    if explicit:
        key = explicit
    else:
        payload = write.get("payload") if isinstance(write.get("payload"), dict) else {}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        key = f"{write_type}:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"
    if len(key) > 160:
        key = f"{write_type}:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
    return key


def _safe_pending_int(value: object, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _cap_pending_writes(writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(writes) <= _MAX_PENDING_WRITES:
        return writes
    return writes[-_MAX_PENDING_WRITES:]


def _pending_write_waiting_for_retry(write: dict[str, Any], current_turn: int) -> bool:
    next_retry_turn = _safe_pending_int(write.get("next_retry_turn"), default=current_turn, minimum=0)
    return next_retry_turn > current_turn


def _pending_write_exhausted(write: dict[str, Any]) -> bool:
    attempt_count = _safe_pending_int(write.get("attempt_count"), default=0, minimum=0)
    return attempt_count >= _MAX_SESSION_REPLAY_ATTEMPTS


def _mark_pending_write_failed(write: dict[str, Any], current_turn: int, exc: Exception) -> dict[str, Any]:
    failed = dict(write)
    attempt_count = _safe_pending_int(failed.get("attempt_count"), default=0, minimum=0) + 1
    failed["attempt_count"] = attempt_count
    failed["last_error"] = str(exc)[:500]
    failed["next_retry_turn"] = current_turn + min(8, 2 ** max(0, attempt_count - 1))
    return failed


def _strip_write_metadata(payload: dict) -> dict:
    return {
        key: value
        for key, value in dict(payload or {}).items()
        if key not in {"_idempotency_key", "idempotency_key"}
    }


def _profile_context_changed(previous_profile: dict | None, next_profile: dict | None) -> bool:
    return profile_context_changed(previous_profile, next_profile)


def _should_clear_active_proposal_for_profile_change(state: GraphState, next_profile: dict | None) -> bool:
    has_active_context = bool(
        state.get("active_proposal")
        or state.get("proposed_plan")
        or state.get("awaiting_plan_confirmation")
        or state.get("pending_sequential_plan")
    )
    return has_active_context and _profile_context_changed(state.get("user_profile"), next_profile)


def _profile_context_changed_fields(previous_profile: dict | None, next_profile: dict | None) -> list[str]:
    return profile_context_changed_fields(previous_profile, next_profile)


def _clear_active_proposal_updates() -> dict[str, Any]:
    return {
        "active_proposal": None,
        "awaiting_plan_confirmation": False,
        "proposed_plan": [],
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "pending_sequential_plan": None,
    }


async def _load_user_profile_with_fallback(
    *,
    deps: NodeDeps,
    user_id: str,
    fallback: dict | None,
    context: str,
) -> tuple[dict, bool]:
    try:
        profile = await deps.was.get_user_profile(user_id)
        logger.info("WAS user_profile load succeeded: user_id=%s context=%s", user_id, context)
        return _normalize_user_profile(profile), True
    except ExternalServiceError as exc:
        if exc.is_http_status(404):
            logger.info("WAS user_profile missing; using empty default: user_id=%s context=%s", user_id, context)
            deps.trace.record_current_alert(
                severity="warning",
                message="WAS user_profile missing; default profile applied",
                detail={"user_id": user_id, "context": context, "status_code": exc.external_status_code},
            )
            return _normalize_user_profile(None), True

        logger.warning("WAS user_profile load failed; using cached fallback: user_id=%s context=%s error=%s", user_id, context, exc)
        deps.trace.record_current_alert(
            severity="warning",
            message="WAS user_profile load failed; cached fallback applied",
            detail={"user_id": user_id, "context": context, "error": str(exc)},
        )
        return _normalize_user_profile(fallback), False


async def _load_today_plan_with_fallback(
    *,
    deps: NodeDeps,
    user_id: str,
    fallback: list[dict] | None,
    context: str,
) -> tuple[list[dict], bool]:
    try:
        today_plan = await deps.was.get_today_plan(user_id)
        logger.info("WAS today_plan load succeeded: user_id=%s context=%s items=%s", user_id, context, len(today_plan or []))
        return _normalize_today_plan(today_plan), True
    except ExternalServiceError as exc:
        if exc.is_http_status(404):
            logger.info("WAS today_plan missing; using empty default: user_id=%s context=%s", user_id, context)
            deps.trace.record_current_alert(
                severity="warning",
                message="WAS today_plan missing; empty plan applied",
                detail={"user_id": user_id, "context": context, "status_code": exc.external_status_code},
            )
            return [], True

        logger.warning("WAS today_plan load failed; using cached fallback: user_id=%s context=%s error=%s", user_id, context, exc)
        deps.trace.record_current_alert(
            severity="warning",
            message="WAS today_plan load failed; cached fallback applied",
            detail={"user_id": user_id, "context": context, "error": str(exc)},
        )
        return _normalize_today_plan(fallback), False


def _normalize_user_profile(profile: object) -> dict:
    normalized = dict(_safe_dict(profile))
    normalized.setdefault("allergies", [])
    normalized.setdefault("injury_history", [])
    return normalized


def _normalize_today_plan(today_plan: object) -> list[dict]:
    if not isinstance(today_plan, list):
        return []
    normalized: list[dict] = []
    for item in today_plan[:80]:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        for key in ("id", "type", "name", "detail", "day", "plan_type"):
            if key in next_item:
                next_item[key] = _bounded_state_text(next_item.get(key), limit=160)
        if "ex_list" in next_item:
            next_item["ex_list"] = _normalize_today_exercises(next_item.get("ex_list"))
        normalized.append(next_item)
    return normalized


def _normalize_today_exercises(exercises: object) -> list[dict]:
    if not isinstance(exercises, list):
        return []
    normalized: list[dict] = []
    for exercise in exercises[:20]:
        if not isinstance(exercise, dict):
            continue
        next_exercise = dict(exercise)
        if "exercise_name" in next_exercise:
            next_exercise["exercise_name"] = _bounded_state_text(next_exercise.get("exercise_name"), limit=120)
        normalized.append(next_exercise)
    return normalized


def _bounded_state_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
