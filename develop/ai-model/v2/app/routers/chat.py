"""FastAPI /chat endpoint."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi import Depends

from app.core.config import get_settings
from app.core.conversation_state import (
    ACTIVE_PROPOSAL_STALE_TURNS,
    active_proposal_is_mixed_domain,
    append_recent_turn,
    build_recent_turn,
    empty_context_resolution,
    empty_recent_dialogue,
    evolve_active_proposal,
    merge_profile_override_for_plan_context,
    profile_override_changes_plan_context,
    sync_proposal_fields,
)
from app.core.exceptions import ExternalServiceError
from app.core.internal_auth import require_internal_api_key
from app.core.lifespan import update_session_activity
from app.core.session_lock import acquire_session_lock
from app.core.trace_store import bind_trace, reset_trace, timed_ms
from app.core.was_outbox import (
    enqueue_was_outbox,
    mark_was_outbox_succeeded,
    reconcile_pending_writes_with_outbox,
)
from app.graph.nodes.feedback import execute_feedback
from app.graph.nodes.intent import INTENT_APPROVAL, INTENT_RECORD
from app.graph.nodes.was_write import execute_was_writes
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.state import GraphState
from app.services.langsmith_quality import (
    export_quality_trace,
    record_quality_for_trace,
)

logger = logging.getLogger(__name__)

_MAX_PENDING_WRITES = 24
_RECENT_DIALOGUE_LIMIT = 4
_ALLOWED_DIALOGUE_ACTION_INTENTS = {
    "create",
    "modify",
    "info",
    "record",
    "approval",
    "care",
    "casual",
    "safety",
    "fallback",
    "home_recommendation",
}
_ALLOWED_DIALOGUE_DOMAINS = {"workout", "diet", "profile", "general", "none"}
_ALLOWED_DIALOGUE_SUPPORT_MODES = {"care", "normal"}
_ALLOWED_DIALOGUE_REFERENCES = {
    "none",
    "active_proposal",
    "today_plan",
    "previous_answer",
    "recent_chat",
    "user_memory",
}
_ALLOWED_DIALOGUE_STATE_EFFECTS = {
    "none",
    "proposal_created",
    "proposal_updated",
    "proposal_approved",
    "profile_recorded",
    "plan_checked",
    "plan_deleted",
    "clarification_requested",
}

REQUEST_TIMEOUT = 120
router = APIRouter(prefix="/chat", tags=["chat"])
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()


async def _get_session_lock(session_id: str) -> asyncio.Lock:
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[session_id] = lock
        return lock


def _checkpoint_thread_id(user_id: str, session_id: str) -> str:
    identity = json.dumps([user_id, session_id], ensure_ascii=False, separators=(",", ":"))
    return f"chat:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _build_initial_state(req: ChatRequest) -> GraphState:
    initial_state: GraphState = {
        "user_id": req.user_id,
        "user_message": req.user_message,
        "request_kind": "chat",
        "user_profile": None,
        "effective_user_profile": None,
        "pending_profile_overlay": None,
        "profile_override_applied": False,
        "today_plan": None,
        "turn_count": 0,
        "is_session_start": True,
        "intent": "",
        "action_intent": None,
        "domain": "general",
        "support_mode": "normal",
        "ambiguous": False,
        "routing_diagnostics": None,
        "context_resolution": empty_context_resolution(),
        "confidence": 0.0,
        "emotion": None,
        "previous_intent": None,
        "previous_emotion": None,
        "requires_past_memory": False,
        "should_save_episode": False,
        "short_term_memory_query": False,
        "has_fact_change": False,
        "record_type": None,
        "profile_changes": None,
        "is_today": None,
        "modify_target": None,
        "search_targets": [],
        "modify_plan_context": None,
        "profile_constraints": None,
        "retrieval_decision": None,
        "fast_intent_contract": None,
        "target_resource_context": None,
        "acsm_boundary": None,
        "diet_boundary": None,
        "search_results": [],
        "search_quality": "ok",
        "search_retry_count": 0,
        "search_query": None,
        "pending_writes": [],
        "awaiting_plan_confirmation": False,
        "active_proposal": None,
        "pending_sequential_plan": None,
        "recent_dialogue": empty_recent_dialogue(),
        "draft_response": None,
        "draft_components": None,
        "proposed_plan": None,
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "home_recommendation_scope": None,
        "home_recommendations": None,
        "home_recommendation_recent": None,
        "intimacy_level": 1,
        "resolved_persona_id": None,
        "profile_sync_version": 0,
        "response": None,
        "force_regenerate": False,
        "validation_report": None,
        "validation_retry_count": 0,
        "generation_quality_flags": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
        "fallback_count": 0,
        "needs_clarification": False,
    }
    if req.user_profile_override:
        initial_state["user_profile"] = req.user_profile_override
        initial_state["profile_override_applied"] = True
    return initial_state


def _build_resumed_state(req: ChatRequest, saved_values: dict[str, Any]) -> GraphState:
    resumed_state = _build_initial_state(req)
    saved_turn_count = _safe_int(saved_values.get("turn_count"), default=0)
    hydrated_active_proposal = _hydrate_active_proposal(saved_values, current_turn=saved_turn_count)
    profile_context_changed = bool(
        req.user_profile_override
        and profile_override_changes_plan_context(
            saved_values.get("user_profile") or {},
            req.user_profile_override,
        )
    )
    if profile_context_changed:
        hydrated_active_proposal = None
    resumed_state.update(
        {
            "user_profile": saved_values.get("user_profile"),
            "effective_user_profile": saved_values.get("effective_user_profile"),
            "pending_profile_overlay": saved_values.get("pending_profile_overlay"),
            "profile_override_applied": False,
            "today_plan": saved_values.get("today_plan"),
            "turn_count": saved_turn_count,
            "is_session_start": False,
            "previous_intent": saved_values.get("previous_intent"),
            "previous_emotion": saved_values.get("previous_emotion"),
            "pending_writes": saved_values.get("pending_writes") or [],
            "awaiting_plan_confirmation": False if profile_context_changed else bool(saved_values.get("awaiting_plan_confirmation")) or bool(hydrated_active_proposal),
            "active_proposal": hydrated_active_proposal,
            "pending_sequential_plan": None if profile_context_changed else _bounded_pending_sequential_plan(
                saved_values.get("pending_sequential_plan"),
                current_turn=saved_turn_count,
            ),
            "recent_dialogue": _hydrate_recent_dialogue(saved_values),
            "proposed_plan": None if profile_context_changed else saved_values.get("proposed_plan"),
            "proposed_plan_type": None if profile_context_changed else saved_values.get("proposed_plan_type"),
            "proposed_plan_action": None if profile_context_changed else saved_values.get("proposed_plan_action"),
            "intimacy_level": _safe_int(saved_values.get("intimacy_level"), default=1),
            "profile_sync_version": _safe_int(saved_values.get("profile_sync_version"), default=0) + (1 if profile_context_changed else 0),
            "fallback_count": _safe_int(saved_values.get("fallback_count"), default=0),
        }
    )
    if req.user_profile_override:
        saved_profile = dict(saved_values.get("user_profile") or {})
        merged_profile = merge_profile_override_for_plan_context(saved_profile, req.user_profile_override)
        resumed_state["user_profile"] = merged_profile
        resumed_state["effective_user_profile"] = merged_profile
        resumed_state["pending_profile_overlay"] = None
        resumed_state["profile_override_applied"] = True
    return resumed_state


def _effective_profile(result: GraphState) -> dict[str, Any]:
    return _safe_dict(result.get("effective_user_profile")) or _safe_dict(result.get("user_profile"))


def _pending_write_types(writes: object) -> list[str]:
    if not isinstance(writes, list):
        return []
    return sorted(
        {
            str(write.get("write_type"))
            for write in writes
            if isinstance(write, dict) and write.get("write_type")
        }
    )


def _build_debug_state(trace_id: str, result: GraphState) -> dict[str, Any]:
    effective_profile = _effective_profile(result)
    pending_write_types = _pending_write_types(result.get("pending_writes") or [])
    return {
        "trace_id": trace_id,
        "search_results_count": len(_safe_list(result.get("search_results"))),
        "search_quality": result.get("search_quality"),
        "action_intent": result.get("action_intent"),
        "record_type": result.get("record_type"),
        "domain": result.get("domain"),
        "support_mode": result.get("support_mode"),
        "ambiguous": result.get("ambiguous"),
        "routing_diagnostics": result.get("routing_diagnostics"),
        "draft_components": result.get("draft_components"),
        "proposed_plan_count": len(_safe_plan_items(result.get("proposed_plan"))),
        "proposed_plan": result.get("proposed_plan"),
        "proposed_plan_type": result.get("proposed_plan_type"),
        "proposed_plan_action": result.get("proposed_plan_action"),
        "awaiting_plan_confirmation": result.get("awaiting_plan_confirmation"),
        "active_proposal": result.get("active_proposal"),
        "pending_sequential_plan": result.get("pending_sequential_plan"),
        "recent_dialogue": result.get("recent_dialogue"),
        "selected_ai_persona": effective_profile.get(
            "selected_ai_persona"
        ),
        "resolved_persona_id": result.get("resolved_persona_id"),
        "profile_constraints": result.get("profile_constraints"),
        "retrieval_decision": result.get("retrieval_decision"),
        "validation_report": result.get("validation_report"),
        "generation_quality_flags": result.get("generation_quality_flags"),
        "profile_sync_version": result.get("profile_sync_version"),
        "profile_write_pending": "profile" in pending_write_types,
        "pending_writes_count": len(_safe_list(result.get("pending_writes"))),
        "pending_write_types": pending_write_types,
        "pending_profile_overlay": result.get("pending_profile_overlay"),
        "intimacy_level": result.get("intimacy_level"),
        "needs_clarification": result.get("needs_clarification"),
        "user_profile_mbti": effective_profile.get("mbti"),
        "profile_signal_summary": _profile_signal_summary(effective_profile),
        "proposed_plan_preview": _preview_proposed_plan(result.get("proposed_plan") or []),
    }


def _resolve_plan_write_fields(result: GraphState) -> tuple[list[dict] | None, str | None, str | None]:
    proposed_plan = result.get("proposed_plan")
    proposed_plan_type = result.get("proposed_plan_type")
    proposed_plan_action = result.get("proposed_plan_action")

    active_proposal = _safe_dict(result.get("active_proposal"))
    if not proposed_plan and active_proposal.get("items"):
        proposed_plan = active_proposal.get("items")
    if proposed_plan_type not in {"workout", "diet", "bundle"} and active_proposal.get("domain") in {"workout", "diet", "bundle"}:
        proposed_plan_type = active_proposal.get("domain")
    if proposed_plan_action not in {"create", "update"} and active_proposal.get("write_mode") in {"create", "update"}:
        proposed_plan_action = active_proposal.get("write_mode")

    proposed_plan = _safe_plan_items(proposed_plan)
    if not proposed_plan:
        return None, proposed_plan_type, proposed_plan_action
    return proposed_plan, proposed_plan_type, proposed_plan_action


def _build_state_summary(result: GraphState) -> dict[str, Any]:
    effective_profile = _effective_profile(result)
    pending_write_types = _pending_write_types(result.get("pending_writes") or [])
    return {
        "intent": result.get("intent"),
        "action_intent": result.get("action_intent"),
        "domain": result.get("domain"),
        "support_mode": result.get("support_mode"),
        "ambiguous": result.get("ambiguous"),
        "routing_diagnostics": result.get("routing_diagnostics"),
        "search_quality": result.get("search_quality"),
        "record_type": result.get("record_type"),
        "modify_target": result.get("modify_target"),
        "resolved_persona_id": result.get("resolved_persona_id"),
        "profile_constraints": result.get("profile_constraints"),
        "retrieval_decision": result.get("retrieval_decision"),
        "validation_report": result.get("validation_report"),
        "generation_quality_flags": result.get("generation_quality_flags"),
        "profile_sync_version": result.get("profile_sync_version"),
        "search_results_count": len(_safe_list(result.get("search_results"))),
        "proposed_plan_type": result.get("proposed_plan_type"),
        "proposed_plan_action": result.get("proposed_plan_action"),
        "proposed_plan_count": len(_safe_plan_items(result.get("proposed_plan"))),
        "awaiting_plan_confirmation": result.get("awaiting_plan_confirmation"),
        "active_proposal_present": bool(result.get("active_proposal")),
        "pending_sequential_plan": result.get("pending_sequential_plan"),
        "recent_dialogue_turns": len(_safe_list(_safe_dict(result.get("recent_dialogue")).get("recent_turns"))),
        "pending_writes_count": len(_safe_list(result.get("pending_writes"))),
        "pending_write_types": pending_write_types,
        "profile_write_pending": "profile" in pending_write_types,
        "needs_clarification": result.get("needs_clarification"),
        "draft_components": result.get("draft_components"),
        "profile_signal_summary": _profile_signal_summary(effective_profile),
        "search_results_preview": _preview_search_results(result.get("search_results")),
        "proposed_plan_preview": _preview_proposed_plan(result.get("proposed_plan")),
    }


def _profile_signal_summary(profile: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "age",
        "gender",
        "sex",
        "weight",
        "height",
        "bmi",
        "activity_level",
        "activityLevel",
        "exercise_level",
        "fitness_level",
        "exercise_frequency",
        "workout_frequency",
        "frequency_per_week",
        "weekly_workouts",
        "target_workouts_per_week",
        "preferred_workout_days",
        "goal",
        "primary_goal",
        "exercise_goal",
        "training_goal",
        "diet_goal",
        "diet_type",
        "dietary_preferences",
        "foods_to_avoid",
        "lifestyle",
        "schedule",
        "available_time_minutes",
        "injury_history",
        "medical_history",
        "medical_conditions",
        "conditions",
        "pain_points",
        "allergies",
        "allergy",
        "otherAllergy",
        "other_allergy",
        "dietary_restrictions",
        "context_notes",
        "social_orientation",
        "personality_axis",
        "personality_type",
        "personality",
        "exercise_style",
        "introversion_extroversion",
        "mbti",
        "emotional_context",
        "selected_ai_persona",
    )
    return {key: profile.get(key) for key in keys if profile.get(key) not in (None, "", [])}


def _preview_search_results(results: object, *, limit: int = 5) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for item in _safe_list(results)[:limit]:
        if not isinstance(item, dict):
            continue
        preview.append(
            {
                "id": item.get("id"),
                "source": item.get("source"),
                "score": item.get("score"),
                "metadata": _safe_dict(item.get("metadata")),
                "text": str(item.get("text") or "")[:240],
            }
        )
    return preview


def _preview_proposed_plan(plan: object, *, limit: int = 6) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for item in _safe_plan_items(plan)[:limit]:
        exercises = []
        for exercise in _safe_list(item.get("ex_list")):
            if not isinstance(exercise, dict):
                continue
            exercises.append(
                {
                    "exercise_name": exercise.get("exercise_name"),
                    "sets": exercise.get("sets"),
                    "reps": exercise.get("reps"),
                    "duration_minutes": exercise.get("duration_minutes"),
                    "calories": exercise.get("calories"),
                }
            )
        preview.append(
            {
                "name": item.get("name"),
                "detail": item.get("detail"),
                "day": item.get("day"),
                "ex_list": exercises[:5],
            }
        )
    return preview


def _record_quality_and_schedule_export(
    *,
    request: Request,
    background_tasks: BackgroundTasks,
    trace_store,
    trace_id: str,
) -> None:
    record_quality_for_trace(trace_store, trace_id)
    exporter = getattr(request.app.state, "langsmith_quality", None)
    if exporter and exporter.configured:
        background_tasks.add_task(
            export_quality_trace,
            exporter=exporter,
            trace_store=trace_store,
            trace_id=trace_id,
        )


def _safe_int(value: object, *, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_plan_items(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in _safe_list(value) if isinstance(item, dict)]


def _hydrate_active_proposal(saved_values: dict[str, Any], *, current_turn: int) -> dict[str, Any] | None:
    active_proposal = saved_values.get("active_proposal")
    if active_proposal:
        return _sanitize_active_proposal(active_proposal, current_turn=current_turn)

    proposed_plan = saved_values.get("proposed_plan") or []
    proposed_plan_type = saved_values.get("proposed_plan_type")
    if not isinstance(proposed_plan, list) or not proposed_plan or proposed_plan_type not in {"workout", "diet", "bundle"}:
        return None
    if not all(isinstance(item, dict) for item in proposed_plan):
        return None
    if active_proposal_is_mixed_domain({"domain": proposed_plan_type, "items": proposed_plan}):
        proposed_plan_type = "bundle"

    return {
        "domain": proposed_plan_type,
        "write_mode": "update" if saved_values.get("proposed_plan_action") == "update" else "create",
        "items": proposed_plan,
        "summary": f"{'운동' if proposed_plan_type == 'workout' else '식단'} 제안",
        "last_used_turn": current_turn,
    }


def _sanitize_active_proposal(active_proposal: object, *, current_turn: int) -> dict[str, Any] | None:
    if not isinstance(active_proposal, dict) or active_proposal_is_mixed_domain(active_proposal):
        return None
    domain = str(active_proposal.get("domain") or "")
    if domain not in {"workout", "diet", "bundle"}:
        return None
    items = active_proposal.get("items")
    if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
        return None
    write_mode = "update" if active_proposal.get("write_mode") == "update" else "create"
    last_used_turn = _safe_int(active_proposal.get("last_used_turn"), default=current_turn)
    if last_used_turn > current_turn:
        last_used_turn = current_turn
    if last_used_turn < 0:
        last_used_turn = 0
    if current_turn - last_used_turn >= ACTIVE_PROPOSAL_STALE_TURNS:
        return None
    summary = " ".join(str(active_proposal.get("summary") or "").split())[:120]
    return {
        "domain": domain,
        "write_mode": write_mode,
        "items": list(items),
        "summary": summary or f"{'?대룞' if domain == 'workout' else '?앸떒'} ?쒖븞",
        "last_used_turn": last_used_turn,
    }


def _hydrate_recent_dialogue(saved_values: dict[str, Any]) -> dict[str, Any]:
    recent_dialogue = _sanitize_recent_dialogue(saved_values.get("recent_dialogue"))
    if recent_dialogue.get("recent_turns"):
        return recent_dialogue

    messages_raw = saved_values.get("messages")
    messages = messages_raw if isinstance(messages_raw, list) else []
    if not messages:
        return empty_recent_dialogue()

    paired_turns: list[dict[str, Any]] = []
    pending_user_text: str | None = None
    turn_base = _safe_int(saved_values.get("turn_count"), default=0)
    for message in messages[-80:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            pending_user_text = content
            continue
        if role == "assistant" and pending_user_text:
            paired_turns.append(
                {
                    "turn_id": max(turn_base - len(messages) + len(paired_turns) + 1, 0),
                    "user_text": _bounded_dialogue_text(pending_user_text, 320),
                    "assistant_text": _bounded_dialogue_text(content, 320),
                    "user_summary": _bounded_dialogue_text(pending_user_text, 100),
                    "assistant_summary": _bounded_dialogue_text(content, 100),
                    "action_intent": "fallback",
                    "domain": "general",
                    "support_mode": "normal",
                    "referenced_object": "none",
                    "state_effect": "none",
                }
            )
            pending_user_text = None

    if not paired_turns:
        return empty_recent_dialogue()
    return _sanitize_recent_dialogue({"recent_turns": paired_turns})


def _sanitize_recent_dialogue(recent_dialogue: object) -> dict[str, Any]:
    if not isinstance(recent_dialogue, dict):
        return empty_recent_dialogue()
    recent_turns = recent_dialogue.get("recent_turns")
    if not isinstance(recent_turns, list):
        return empty_recent_dialogue()

    sanitized: list[dict[str, Any]] = []
    for turn in recent_turns[-_RECENT_DIALOGUE_LIMIT:]:
        if not isinstance(turn, dict):
            continue
        sanitized.append(
            {
                "turn_id": max(_safe_int(turn.get("turn_id"), default=0), 0),
                "user_text": _bounded_dialogue_text(turn.get("user_text"), 320),
                "assistant_text": _bounded_dialogue_text(turn.get("assistant_text"), 320),
                "user_summary": _bounded_dialogue_text(turn.get("user_summary") or turn.get("user_text"), 100),
                "assistant_summary": _bounded_dialogue_text(
                    turn.get("assistant_summary") or turn.get("assistant_text"),
                    100,
                ),
                "action_intent": _allowed_dialogue_value(
                    turn.get("action_intent"),
                    allowed=_ALLOWED_DIALOGUE_ACTION_INTENTS,
                    default="fallback",
                ),
                "domain": _allowed_dialogue_value(
                    turn.get("domain"),
                    allowed=_ALLOWED_DIALOGUE_DOMAINS,
                    default="general",
                ),
                "support_mode": _allowed_dialogue_value(
                    turn.get("support_mode"),
                    allowed=_ALLOWED_DIALOGUE_SUPPORT_MODES,
                    default="normal",
                ),
                "referenced_object": _allowed_dialogue_value(
                    turn.get("referenced_object"),
                    allowed=_ALLOWED_DIALOGUE_REFERENCES,
                    default="none",
                ),
                "state_effect": _allowed_dialogue_value(
                    turn.get("state_effect"),
                    allowed=_ALLOWED_DIALOGUE_STATE_EFFECTS,
                    default="none",
                ),
            }
        )
    return {"recent_turns": sanitized}


def _allowed_dialogue_value(value: object, *, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _bounded_dialogue_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


async def _persist_bounded_state(
    *,
    graph,
    config: dict,
    previous_state: GraphState,
    result: GraphState,
    response_text: str,
) -> dict[str, Any]:
    previous_active_proposal = previous_state.get("active_proposal")
    next_active_proposal = evolve_active_proposal(previous_active_proposal, result)
    next_recent_dialogue = append_recent_turn(
        previous_state.get("recent_dialogue"),
        build_recent_turn(result, response_text),
    )
    next_pending_sequential_plan = _next_pending_sequential_plan(
        previous_state,
        result,
        next_active_proposal,
    )
    display_updates = {
        **sync_proposal_fields(next_active_proposal),
        "pending_sequential_plan": next_pending_sequential_plan,
        "recent_dialogue": next_recent_dialogue,
    }
    checkpoint_updates = {
        **display_updates,
        **_checkpoint_cleanup_updates(result),
    }
    await graph.aupdate_state(config, checkpoint_updates)
    return display_updates


def _next_pending_sequential_plan(
    previous_state: GraphState,
    result: GraphState,
    next_active_proposal: dict[str, Any] | None,
) -> dict[str, Any] | None:
    current_turn = _safe_int(result.get("turn_count"), default=_safe_int(previous_state.get("turn_count"), default=0))
    if current_turn < 0:
        current_turn = 0
    if "pending_sequential_plan" in result:
        pending = result.get("pending_sequential_plan")
        return _bounded_pending_sequential_plan(pending, current_turn=current_turn)

    previous_pending = _bounded_pending_sequential_plan(
        previous_state.get("pending_sequential_plan"),
        current_turn=current_turn,
    )
    if not previous_pending:
        return None

    if result.get("record_type") in {"plan_delete"}:
        return None
    if result.get("action_intent") in {"safety", "fallback"}:
        return None
    if result.get("needs_clarification") and result.get("action_intent") in {"create", "modify"}:
        return previous_pending
    if result.get("action_intent") in {"create", "modify"} and next_active_proposal:
        queued_domain = str(previous_pending.get("domain") or "")
        active_domain = str(next_active_proposal.get("domain") or "")
        if active_domain and active_domain != queued_domain:
            return None
    return previous_pending


def _bounded_pending_sequential_plan(pending: object, *, current_turn: int) -> dict[str, Any] | None:
    if not isinstance(pending, dict):
        return None
    domain = str(pending.get("domain") or "")
    if domain not in {"workout", "diet"}:
        return None
    created_turn = _safe_int(pending.get("created_turn"), default=current_turn)
    if created_turn > current_turn:
        created_turn = current_turn
    if current_turn - created_turn > 4:
        return None
    reason = " ".join(str(pending.get("reason") or "sequential_plan").split())[:80]
    return {
        "domain": domain,
        "reason": reason or "sequential_plan",
        "created_turn": created_turn,
    }


def _checkpoint_cleanup_updates(result: GraphState) -> dict[str, Any]:
    return {
        "user_message": "",
        "profile_override_applied": False,
        "intent": "",
        "action_intent": None,
        "domain": "general",
        "support_mode": "normal",
        "ambiguous": False,
        "routing_diagnostics": None,
        "context_resolution": empty_context_resolution(),
        "confidence": 0.0,
        "emotion": None,
        "previous_intent": result.get("intent"),
        "previous_emotion": result.get("emotion"),
        "requires_past_memory": False,
        "should_save_episode": False,
        "short_term_memory_query": False,
        "has_fact_change": False,
        "record_type": None,
        "profile_changes": None,
        "is_today": None,
        "modify_target": None,
        "search_targets": [],
        "modify_plan_context": None,
        "profile_constraints": None,
        "retrieval_decision": None,
        "fast_intent_contract": None,
        "target_resource_context": None,
        "acsm_boundary": None,
        "diet_boundary": None,
        "search_results": [],
        "search_quality": "ok",
        "search_retry_count": 0,
        "search_query": None,
        "draft_response": None,
        "draft_components": None,
        "home_recommendation_scope": None,
        "home_recommendations": None,
        "home_recommendation_recent": None,
        "resolved_persona_id": None,
        "response": None,
        "force_regenerate": False,
        "validation_report": None,
        "validation_retry_count": 0,
        "generation_quality_flags": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
        "needs_clarification": False,
    }


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    _: None = Depends(require_internal_api_key),
) -> ChatResponse:
    graph = request.app.state.graph
    deps = request.app.state.deps
    trace_store = request.app.state.trace_store
    settings = get_settings()

    session_id = req.session_id or str(uuid.uuid4())
    checkpoint_thread_id = _checkpoint_thread_id(req.user_id, session_id)
    trace_id = trace_store.start_trace(
        kind="chat",
        user_id=req.user_id,
        session_id=session_id,
        message=req.user_message,
        request_payload=req.model_dump(),
        metadata={"entrypoint": "chat"},
    )
    token = bind_trace(trace_id)
    request_started_at = time.perf_counter()
    config = {"configurable": {"thread_id": checkpoint_thread_id}}
    checkpoint_db_path = str(
        getattr(request.app.state, "checkpoint_db_path", settings.CHECKPOINT_DB_PATH)
    )
    session_lock = await _get_session_lock(checkpoint_thread_id)
    await session_lock.acquire()
    db_session_lock = None

    try:
        try:
            db_session_lock = await acquire_session_lock(checkpoint_db_path, checkpoint_thread_id)
        except TimeoutError:
            logger.warning("Timed out waiting for DB session lock: session=%s", session_id)
            trace_store.record_alert(
                trace_id,
                severity="warning",
                message="Timed out waiting for DB session lock",
                detail={"session_id": session_id},
            )
            retry_message = "잠시만요. 같은 대화 세션에서 이전 요청이 아직 처리 중이에요. 몇 초 뒤 다시 보내주세요."
            trace_store.finish_trace(
                trace_id,
                status="session_lock_timeout",
                response={"response": retry_message},
            )
            _record_quality_and_schedule_export(
                request=request,
                background_tasks=background_tasks,
                trace_store=trace_store,
                trace_id=trace_id,
            )
            return ChatResponse(session_id=session_id, response=retry_message)

        trace_store.record_event(
            trace_id,
            stage="request",
            status="info",
            title="Chat request received",
            detail={"session_id_generated": req.session_id is None},
        )

        saved = await graph.aget_state(config)
        is_new_session = not saved or not saved.values
        trace_store.record_event(
            trace_id,
            stage="session",
            status="ok",
            title="Session state loaded",
            detail={"is_new_session": is_new_session},
        )

        if is_new_session:
            initial_state = _build_initial_state(req)
            profile_context_changed = False
        else:
            saved_values = {
                key: value for key, value in saved.values.items() if key != "ai_persona"
            }
            pending_before_reconcile = saved_values.get("pending_writes") or []
            if pending_before_reconcile:
                try:
                    reconciled_pending, resolved_write_ids = await reconcile_pending_writes_with_outbox(
                        checkpoint_db_path,
                        pending_before_reconcile,
                    )
                    if resolved_write_ids:
                        saved_values["pending_writes"] = reconciled_pending
                        checkpoint_updates: dict[str, Any] = {"pending_writes": reconciled_pending}
                        if _resolved_writes_include_plan_mutation(pending_before_reconcile, resolved_write_ids):
                            refreshed_today_plan = await _refresh_today_plan_after_outbox_resolution(
                                deps,
                                req.user_id,
                            )
                            if refreshed_today_plan is not None:
                                saved_values["today_plan"] = refreshed_today_plan
                                checkpoint_updates["today_plan"] = refreshed_today_plan
                        await graph.aupdate_state(config, checkpoint_updates)
                        trace_store.record_event(
                            trace_id,
                            stage="was_outbox",
                            status="ok",
                            title="Resolved pending WAS writes from outbox",
                            detail={
                                "resolved_count": len(resolved_write_ids),
                                "pending_before": len(pending_before_reconcile),
                                "pending_after": len(reconciled_pending),
                            },
                        )
                except Exception as exc:
                    logger.warning("Failed to reconcile pending WAS writes: %s", exc)
                    trace_store.record_alert(
                        trace_id,
                        severity="warning",
                        message="Failed to reconcile pending WAS writes",
                        detail={"error": str(exc)},
                    )
            profile_context_changed = bool(
                req.user_profile_override
                and profile_override_changes_plan_context(
                    saved_values.get("user_profile") or {},
                    req.user_profile_override,
                )
            )
            initial_state = _build_resumed_state(req, saved_values)
        if profile_context_changed:
            trace_store.record_event(
                trace_id,
                stage="state.active_proposal",
                status="ok",
                title="Active proposal invalidated by profile change",
                detail={
                    "reason": "profile_context_changed",
                    "profile_sync_version": initial_state.get("profile_sync_version"),
                },
            )
        initial_state["checkpoint_db_path"] = checkpoint_db_path

        try:
            result: GraphState = await asyncio.wait_for(
                graph.ainvoke(initial_state, config=config),
                timeout=REQUEST_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Graph request timed out after %ds: session=%s",
                REQUEST_TIMEOUT,
                session_id,
            )
            trace_store.record_alert(
                trace_id,
                severity="error",
                message="Graph request timed out",
                detail={"timeout_seconds": REQUEST_TIMEOUT},
            )
            timeout_message = "죄송해요, 처리 시간이 초과되었어요. 다시 시도해 주세요."
            trace_store.finish_trace(
                trace_id,
                status="timeout",
                response={"response": timeout_message},
            )
            _record_quality_and_schedule_export(
                request=request,
                background_tasks=background_tasks,
                trace_store=trace_store,
                trace_id=trace_id,
            )
            return ChatResponse(session_id=session_id, response=timeout_message)
        except Exception as exc:
            logger.exception("Graph request failed: session=%s", session_id)
            trace_store.record_alert(
                trace_id,
                severity="error",
                message="Graph request failed",
                detail={"error": str(exc)},
            )
            fallback_message = "죄송해요, 초안을 만드는 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."
            trace_store.finish_trace(
                trace_id,
                status="failed",
                response={"response": fallback_message},
            )
            _record_quality_and_schedule_export(
                request=request,
                background_tasks=background_tasks,
                trace_store=trace_store,
                trace_id=trace_id,
            )
            return ChatResponse(session_id=session_id, response=fallback_message)

        response_text = result.get("response") or "죄송해요, 응답을 생성하지 못했어요."
        emotion = result.get("emotion")
        intent = result.get("intent", "")
        plan_sync_applied = False
        was_write_status: dict[str, Any] | None = None

        try:
            bounded_updates = await _persist_bounded_state(
                graph=graph,
                config=config,
                previous_state=initial_state,
                result=result,
                response_text=response_text,
            )
            result.update(bounded_updates)
        except Exception as exc:
            logger.warning("Failed to persist bounded state: %s", exc)
            trace_store.record_alert(
                trace_id,
                severity="warning",
                message="Failed to persist bounded conversation state",
                detail={"error": str(exc)},
            )

        show_debug_state = settings.APP_ENV.strip().casefold() in {"development", "local"}

        background_tasks.add_task(
            update_session_activity,
            checkpoint_db_path,
            checkpoint_thread_id,
        )
        write_proposed_plan, write_proposed_plan_type, write_proposed_plan_action = _resolve_plan_write_fields(result)
        if intent == INTENT_APPROVAL and write_proposed_plan and not result.get("proposed_plan"):
            result["proposed_plan"] = write_proposed_plan
            result["proposed_plan_type"] = write_proposed_plan_type
            result["proposed_plan_action"] = write_proposed_plan_action

        was_write_kwargs = {
            "graph": graph,
            "config": config,
            "deps": deps,
            "trace_store": trace_store,
            "trace_id": trace_id,
            "user_id": req.user_id,
            "intent": intent,
            "response": response_text,
            "record_type": result.get("record_type"),
            "profile_changes": result.get("profile_changes"),
            "today_plan": result.get("today_plan"),
            "search_results": result.get("search_results"),
            "modify_target": result.get("modify_target"),
            "modify_plan_context": result.get("modify_plan_context"),
            "proposed_plan": write_proposed_plan,
            "proposed_plan_type": write_proposed_plan_type,
            "proposed_plan_action": write_proposed_plan_action,
            "checkpoint_db_path": checkpoint_db_path,
            "session_id": session_id,
        }
        if intent == INTENT_APPROVAL and write_proposed_plan:
            sync_write_result = await _run_sync_was_write(**was_write_kwargs)
            result.update(sync_write_result.get("checkpoint_updates") or {})
            plan_sync_applied = bool(sync_write_result["write_succeeded"] and not sync_write_result["pending"])
            was_write_status = _was_write_status(sync_write_result, mode="sync")
            response_text = _approval_response_for_write_status(
                response_text,
                sync_write_result,
                write_proposed_plan_type,
            )
        elif intent == INTENT_RECORD:
            sync_write_result = await _run_sync_was_write(**was_write_kwargs)
            result.update(sync_write_result.get("checkpoint_updates") or {})
            plan_sync_applied = bool(
                result.get("record_type") in {"plan_delete", "plan_check"}
                and sync_write_result["write_succeeded"]
                and not sync_write_result["pending"]
            )
            was_write_status = _was_write_status(sync_write_result, mode="sync")
        elif _has_was_write_work(
            intent=intent,
            record_type=result.get("record_type"),
            profile_changes=result.get("profile_changes"),
            proposed_plan=write_proposed_plan,
        ):
            background_tasks.add_task(
                _was_write_and_save_pending,
                **was_write_kwargs,
            )
            was_write_status = _was_write_status(None, mode="background_scheduled", state=result)
        else:
            was_write_status = _was_write_status(None, mode="none", state=result)
        background_tasks.add_task(
            execute_feedback,
            deps=deps,
            user_id=req.user_id,
            user_message=req.user_message,
            response=response_text,
            should_save_episode=result.get("should_save_episode", False),
            emotion_label=emotion["label"] if emotion else "중립",
            emotion_intensity=emotion["intensity"] if emotion else 0.0,
        )

        trace_store.record_event(
            trace_id,
            stage="response",
            status="ok",
            title="Response sent to caller",
            detail={
                "intent": intent,
                "response_length": len(response_text),
                "plan_sync_applied": plan_sync_applied,
            },
            duration_ms=timed_ms(request_started_at),
        )
        trace_store.finish_trace(
            trace_id,
            status="response_sent",
            response={
                "intent": intent,
                "response": response_text,
                "emotion": emotion,
                "plan_sync_applied": plan_sync_applied,
                "was_write_status": was_write_status,
            },
            state_summary=_build_state_summary(result),
        )
        _record_quality_and_schedule_export(
            request=request,
            background_tasks=background_tasks,
            trace_store=trace_store,
            trace_id=trace_id,
        )

        return ChatResponse(
            session_id=session_id,
            response=response_text,
            intent=intent,
            emotion=emotion,
            draft_response=result.get("draft_response"),
            plan_sync_applied=plan_sync_applied,
            was_write_status=was_write_status,
            pending_writes_count=len(_safe_list(result.get("pending_writes"))),
            pending_write_types=_pending_write_types(result.get("pending_writes")),
            debug_state=_build_debug_state(trace_id, result) if show_debug_state else None,
        )
    finally:
        if db_session_lock is not None:
            await db_session_lock.release()
        session_lock.release()
        reset_trace(token)


async def _was_write_and_save_pending(
    graph,
    config: dict,
    deps,
    trace_store,
    trace_id: str,
    user_id: str,
    intent: str,
    response: str,
    record_type,
    profile_changes,
    today_plan,
    search_results,
    modify_target,
    modify_plan_context,
    proposed_plan,
    proposed_plan_type,
    proposed_plan_action,
    checkpoint_db_path: str | None = None,
    session_id: str | None = None,
) -> None:
    token = bind_trace(trace_id)
    try:
        trace_store.record_event(
            trace_id,
            stage="was_write",
            status="info",
            title="Background WAS write started",
            detail={"intent": intent, "record_type": record_type},
        )
        write_result = await execute_was_writes(
            deps=deps,
            user_id=user_id,
            intent=intent,
            response=response,
            record_type=record_type,
            profile_changes=profile_changes,
            today_plan=today_plan,
            search_results=search_results,
            modify_target=modify_target,
            modify_plan_context=modify_plan_context,
            proposed_plan=proposed_plan,
            proposed_plan_type=proposed_plan_type,
            proposed_plan_action=proposed_plan_action,
        )
        await _apply_was_write_result(
            graph=graph,
            config=config,
            deps=deps,
            trace_store=trace_store,
            trace_id=trace_id,
            user_id=user_id,
            intent=intent,
            record_type=record_type,
            proposed_plan=proposed_plan,
            write_result=write_result,
            checkpoint_db_path=checkpoint_db_path,
            session_id=session_id,
        )
    finally:
        reset_trace(token)


async def _run_sync_was_write(
    graph,
    config: dict,
    deps,
    trace_store,
    trace_id: str,
    user_id: str,
    intent: str,
    response: str,
    record_type,
    profile_changes,
    today_plan,
    search_results,
    modify_target,
    modify_plan_context,
    proposed_plan,
    proposed_plan_type,
    proposed_plan_action,
    checkpoint_db_path: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    trace_store.record_event(
        trace_id,
        stage="was_write",
        status="info",
        title="Synchronous WAS write started",
        detail={"intent": intent, "record_type": record_type},
    )
    write_result = await execute_was_writes(
        deps=deps,
        user_id=user_id,
        intent=intent,
        response=response,
        record_type=record_type,
        profile_changes=profile_changes,
        today_plan=today_plan,
        search_results=search_results,
        modify_target=modify_target,
        modify_plan_context=modify_plan_context,
        proposed_plan=proposed_plan,
        proposed_plan_type=proposed_plan_type,
        proposed_plan_action=proposed_plan_action,
    )
    checkpoint_updates = await _apply_was_write_result(
        graph=graph,
        config=config,
        deps=deps,
        trace_store=trace_store,
        trace_id=trace_id,
        user_id=user_id,
        intent=intent,
        record_type=record_type,
        proposed_plan=proposed_plan,
        write_result=write_result,
        checkpoint_db_path=checkpoint_db_path,
        session_id=session_id,
    )
    return {**write_result, "checkpoint_updates": checkpoint_updates}


def _resolved_writes_include_plan_mutation(
    pending_writes: list[dict[str, Any]],
    resolved_write_ids: list[str],
) -> bool:
    resolved = {str(write_id) for write_id in resolved_write_ids}
    return any(
        str(write.get("write_id") or write.get("idempotency_key") or "") in resolved
        and write.get("write_type") in {"plan_check", "plan_create", "plan_update", "plan_delete"}
        for write in pending_writes
        if isinstance(write, dict)
    )


async def _refresh_today_plan_after_outbox_resolution(deps, user_id: str) -> list[dict[str, Any]] | None:
    try:
        return await deps.was.get_today_plan(user_id)
    except ExternalServiceError as exc:
        if exc.is_http_status(404):
            return []
        logger.warning("Failed to refresh today_plan after outbox resolution: %s", exc)
    except Exception as exc:
        logger.warning("Failed to refresh today_plan after outbox resolution: %s", exc)
    return None


async def _apply_was_write_result(
    *,
    graph,
    config: dict,
    deps,
    trace_store,
    trace_id: str,
    user_id: str,
    intent: str,
    record_type,
    proposed_plan,
    write_result: dict,
    checkpoint_db_path: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    pending = write_result["pending"]
    write_succeeded = write_result["write_succeeded"]
    applied_profile_changes = write_result.get("applied_profile_changes") or {}
    succeeded_write_ids = _safe_list(write_result.get("succeeded_write_ids"))

    updates = {}
    saved_values: dict[str, Any] = {}
    try:
        saved = await graph.aget_state(config)
        saved_values = dict(saved.values or {})
    except Exception as exc:
        logger.warning("Failed to read checkpoint before WAS update merge: %s", exc)

    if pending:
        updates["pending_writes"] = _merge_pending_writes(
            saved_values.get("pending_writes") or [],
            pending,
        )
        if checkpoint_db_path:
            try:
                await enqueue_was_outbox(
                    checkpoint_db_path,
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    writes=pending,
                )
            except Exception as exc:
                logger.error("Failed to enqueue WAS outbox writes: %s", exc)
                trace_store.record_alert(
                    trace_id,
                    severity="error",
                    message="Failed to enqueue WAS outbox writes",
                    detail={"error": str(exc), "pending_count": len(pending)},
                )

    if checkpoint_db_path and succeeded_write_ids:
        for write_id in succeeded_write_ids:
            try:
                await mark_was_outbox_succeeded(checkpoint_db_path, write_id)
            except Exception as exc:
                logger.warning("Failed to mark WAS outbox succeeded: %s", exc)

    if applied_profile_changes:
        profile_version = await _mark_profile_updated(deps, user_id)
        updates.update(
            {
                "user_profile": _merge_profile_changes(
                    saved_values.get("user_profile") or {},
                    applied_profile_changes,
                ),
                "effective_user_profile": None,
                "pending_profile_overlay": None,
                "active_proposal": None,
                "awaiting_plan_confirmation": False,
                "proposed_plan": None,
                "proposed_plan_type": None,
                "proposed_plan_action": None,
                "pending_sequential_plan": None,
            }
        )
        if profile_version is not None:
            updates["profile_sync_version"] = profile_version

    if not pending and write_succeeded and (
        (intent == INTENT_APPROVAL and proposed_plan)
        or (intent == INTENT_RECORD and record_type in {"plan_delete", "plan_check"})
    ):
        refreshed_today_plan = None
        try:
            refreshed_today_plan = await deps.was.get_today_plan(user_id)
        except ExternalServiceError as exc:
            if exc.is_http_status(404):
                refreshed_today_plan = []
                logger.info("today_plan missing after plan write; applying empty plan: user_id=%s", user_id)
            else:
                logger.warning("Failed to refresh today_plan after plan write: %s", exc)
        except Exception as exc:
            logger.warning("Failed to refresh today_plan after plan write: %s", exc)

        updates.setdefault("pending_writes", saved_values.get("pending_writes") or [])
        if intent == INTENT_APPROVAL:
            updates.update(
                {
                    "awaiting_plan_confirmation": False,
                    "active_proposal": None,
                    "proposed_plan": None,
                    "proposed_plan_type": None,
                    "proposed_plan_action": None,
                }
            )
        if refreshed_today_plan is not None:
            updates["today_plan"] = refreshed_today_plan

    if updates:
        try:
            await graph.aupdate_state(config, updates)
            if pending:
                logger.warning("Saved %d pending writes to checkpoint", len(pending))
        except Exception as exc:
            logger.error("Failed to update pending writes: %s", exc)
            trace_store.record_alert(
                trace_id,
                severity="error",
                message="Failed to update checkpoint after WAS write",
                detail={"error": str(exc)},
            )

    if pending:
        trace_store.record_alert(
            trace_id,
            severity="warning",
            message="WAS write failed and was kept as pending",
            detail={"pending_count": len(pending)},
        )
    else:
        trace_store.record_event(
            trace_id,
            stage="was_write",
            status="ok",
            title="WAS write completed",
            detail={"write_succeeded": write_succeeded},
        )
    return updates


async def _mark_profile_updated(deps, user_id: str) -> int | None:
    marker = getattr(deps.profile_sync, "mark_profile_updated", None)
    if marker:
        return await marker(user_id)
    getter = getattr(deps.profile_sync, "get_profile_version", None)
    if getter:
        return await getter(user_id)
    return None


def _merge_profile_changes(profile: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    clean_changes = {
        key: value
        for key, value in _safe_dict(changes).items()
        if key not in {"item_id", "plan_type", "target_scope", "target_dates", "_idempotency_key", "idempotency_key"}
    }
    merged = merge_profile_override_for_plan_context(_safe_dict(profile), clean_changes)
    for key, value in clean_changes.items():
        merged[key] = value
    merged.setdefault("allergies", [])
    merged.setdefault("injury_history", [])
    return merged


def _merge_pending_writes(existing: object, new_writes: object) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for write in [*_safe_list(existing), *_safe_list(new_writes)]:
        if not isinstance(write, dict):
            continue
        key = _pending_write_key(write)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized = dict(write)
        if not isinstance(normalized.get("payload"), dict):
            continue
        normalized.setdefault("write_id", key)
        normalized.setdefault("idempotency_key", key)
        normalized["attempt_count"] = _safe_pending_int(normalized.get("attempt_count"), default=0, minimum=0)
        normalized["next_retry_turn"] = _safe_pending_int(normalized.get("next_retry_turn"), default=0, minimum=0)
        if "last_error" in normalized:
            normalized["last_error"] = str(normalized.get("last_error") or "")[:500]
        merged.append(normalized)
    return merged[-_MAX_PENDING_WRITES:]


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


def _has_was_write_work(
    *,
    intent: str,
    record_type: str | None,
    profile_changes: dict[str, Any] | None,
    proposed_plan: list[dict] | None,
) -> bool:
    if intent == INTENT_APPROVAL and proposed_plan:
        return True
    if intent != INTENT_RECORD:
        return False
    if record_type in {"profile", "plan_check", "plan_delete"} and profile_changes:
        return True
    return False


def _was_write_status(
    write_result: dict[str, Any] | None,
    *,
    mode: str,
    state: GraphState | None = None,
) -> dict[str, Any]:
    pending = (
        _safe_list(write_result.get("pending"))
        if write_result
        else _safe_list(_safe_dict(state).get("pending_writes"))
    )
    return {
        "mode": mode,
        "write_succeeded": bool(write_result.get("write_succeeded")) if write_result else None,
        "pending_count": len(pending),
        "pending_write_types": _pending_write_types(pending),
        "failed_write_types": _safe_list(write_result.get("failed_write_types")) if write_result else [],
        "succeeded_write_ids": _safe_list(write_result.get("succeeded_write_ids")) if write_result else [],
        "applied_profile": bool(write_result.get("applied_profile_changes")) if write_result else False,
    }


def _approval_response_for_write_status(
    response_text: str,
    write_result: dict[str, Any],
    proposed_plan_type: str | None,
) -> str:
    pending = _safe_list(write_result.get("pending"))
    succeeded = bool(write_result.get("write_succeeded"))
    if not pending and succeeded:
        return response_text

    plan_label = "식단" if proposed_plan_type == "diet" else "운동" if proposed_plan_type == "workout" else "플랜"
    if pending:
        return (
            f"{plan_label} 저장 요청은 접수했어요.\n\n"
            "지금은 서버 저장이 지연돼 대기열에 넣어둘게요. 잠시 후 자동으로 다시 반영됩니다."
        )

    failed_types = _pending_write_types(write_result.get("pending")) or _safe_list(write_result.get("failed_write_types"))
    suffix = f" ({', '.join(failed_types)})" if failed_types else ""
    return (
        f"{plan_label} 저장을 완료하지 못했어요{suffix}.\n\n"
        "플랜 내용은 유지해둘게요. 잠시 후 다시 저장을 눌러주세요."
    )
