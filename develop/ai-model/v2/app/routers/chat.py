"""FastAPI /chat endpoint."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi import Depends

from app.core.config import get_settings
from app.core.conversation_state import (
    append_recent_turn,
    build_recent_turn,
    empty_context_resolution,
    empty_recent_dialogue,
    evolve_active_proposal,
    sync_proposal_fields,
)
from app.core.exceptions import ExternalServiceError
from app.core.internal_auth import require_internal_api_key
from app.core.lifespan import update_session_activity
from app.core.session_lock import acquire_session_lock
from app.core.trace_store import bind_trace, reset_trace, timed_ms
from app.core.was_outbox import enqueue_was_outbox, mark_was_outbox_succeeded
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
        "search_results": [],
        "search_quality": "ok",
        "search_retry_count": 0,
        "search_query": None,
        "pending_writes": [],
        "awaiting_plan_confirmation": False,
        "active_proposal": None,
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
    hydrated_active_proposal = _hydrate_active_proposal(saved_values)
    profile_context_changed = bool(
        req.user_profile_override
        and _profile_override_changes_plan_context(
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
            "turn_count": int(saved_values.get("turn_count", 0) or 0),
            "is_session_start": False,
            "previous_intent": saved_values.get("previous_intent"),
            "previous_emotion": saved_values.get("previous_emotion"),
            "pending_writes": saved_values.get("pending_writes") or [],
            "awaiting_plan_confirmation": False if profile_context_changed else bool(saved_values.get("awaiting_plan_confirmation")) or bool(hydrated_active_proposal),
            "active_proposal": hydrated_active_proposal,
            "recent_dialogue": _hydrate_recent_dialogue(saved_values),
            "proposed_plan": None if profile_context_changed else saved_values.get("proposed_plan"),
            "proposed_plan_type": None if profile_context_changed else saved_values.get("proposed_plan_type"),
            "proposed_plan_action": None if profile_context_changed else saved_values.get("proposed_plan_action"),
            "intimacy_level": int(saved_values.get("intimacy_level", 1) or 1),
            "profile_sync_version": int(saved_values.get("profile_sync_version", 0) or 0) + (1 if profile_context_changed else 0),
            "fallback_count": int(saved_values.get("fallback_count", 0) or 0),
        }
    )
    if req.user_profile_override:
        saved_profile = dict(saved_values.get("user_profile") or {})
        merged_profile = {**saved_profile, **req.user_profile_override}
        resumed_state["user_profile"] = merged_profile
        resumed_state["effective_user_profile"] = merged_profile
        resumed_state["pending_profile_overlay"] = None
        resumed_state["profile_override_applied"] = True
    return resumed_state


_PLAN_CONTEXT_PROFILE_FIELDS = {
    "age",
    "gender",
    "sex",
    "height",
    "weight",
    "bmi",
    "activity_level",
    "exercise_level",
    "fitness_level",
    "goal",
    "primary_goal",
    "diet_goal",
    "diet_type",
    "dietary_restrictions",
    "dietary_preferences",
    "foods_to_avoid",
    "allergies",
    "allergy",
    "injury_history",
    "pain_points",
    "medical_history",
    "medical_conditions",
    "conditions",
    "lifestyle",
    "schedule",
    "available_time_minutes",
    "exercise_frequency",
    "workout_frequency",
    "frequency_per_week",
    "weekly_workouts",
    "target_workouts_per_week",
    "preferred_workout_days",
    "context_notes",
}


def _profile_override_changes_plan_context(saved_profile: dict[str, Any], override: dict[str, Any]) -> bool:
    for field in _PLAN_CONTEXT_PROFILE_FIELDS:
        if field not in override:
            continue
        if _canonical_profile_value(saved_profile.get(field)) != _canonical_profile_value(override.get(field)):
            return True
    return False


def _canonical_profile_value(value: object) -> str:
    if value in (None, "", [], {}, "[]"):
        return ""
    if isinstance(value, list):
        return "|".join(sorted(_canonical_profile_value(item) for item in value if _canonical_profile_value(item)))
    if isinstance(value, dict):
        return "|".join(f"{key}:{_canonical_profile_value(val)}" for key, val in sorted(value.items()))
    return str(value).strip().lower()


def _effective_profile(result: GraphState) -> dict[str, Any]:
    return dict(result.get("effective_user_profile") or result.get("user_profile") or {})


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
        "search_results_count": len(result.get("search_results", [])),
        "search_quality": result.get("search_quality"),
        "action_intent": result.get("action_intent"),
        "record_type": result.get("record_type"),
        "domain": result.get("domain"),
        "support_mode": result.get("support_mode"),
        "ambiguous": result.get("ambiguous"),
        "routing_diagnostics": result.get("routing_diagnostics"),
        "draft_components": result.get("draft_components"),
        "proposed_plan_count": len(result.get("proposed_plan") or []),
        "proposed_plan": result.get("proposed_plan"),
        "proposed_plan_type": result.get("proposed_plan_type"),
        "proposed_plan_action": result.get("proposed_plan_action"),
        "awaiting_plan_confirmation": result.get("awaiting_plan_confirmation"),
        "active_proposal": result.get("active_proposal"),
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
        "pending_writes_count": len(result.get("pending_writes") or []),
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

    active_proposal = result.get("active_proposal") or {}
    if not proposed_plan and active_proposal.get("items"):
        proposed_plan = active_proposal.get("items")
    if proposed_plan_type not in {"workout", "diet"} and active_proposal.get("domain") in {"workout", "diet"}:
        proposed_plan_type = active_proposal.get("domain")
    if proposed_plan_action not in {"create", "update"} and active_proposal.get("write_mode") in {"create", "update"}:
        proposed_plan_action = active_proposal.get("write_mode")

    if not proposed_plan:
        return None, proposed_plan_type, proposed_plan_action
    return list(proposed_plan), proposed_plan_type, proposed_plan_action


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
        "search_results_count": len(result.get("search_results") or []),
        "proposed_plan_type": result.get("proposed_plan_type"),
        "proposed_plan_action": result.get("proposed_plan_action"),
        "proposed_plan_count": len(result.get("proposed_plan") or []),
        "awaiting_plan_confirmation": result.get("awaiting_plan_confirmation"),
        "active_proposal_present": bool(result.get("active_proposal")),
        "recent_dialogue_turns": len((result.get("recent_dialogue") or {}).get("recent_turns") or []),
        "pending_writes_count": len(result.get("pending_writes") or []),
        "pending_write_types": pending_write_types,
        "profile_write_pending": "profile" in pending_write_types,
        "needs_clarification": result.get("needs_clarification"),
        "draft_components": result.get("draft_components"),
        "profile_signal_summary": _profile_signal_summary(effective_profile),
        "search_results_preview": _preview_search_results(result.get("search_results") or []),
        "proposed_plan_preview": _preview_proposed_plan(result.get("proposed_plan") or []),
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


def _preview_search_results(results: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "source": item.get("source"),
            "score": item.get("score"),
            "metadata": item.get("metadata") or {},
            "text": str(item.get("text") or "")[:240],
        }
        for item in results[:limit]
    ]


def _preview_proposed_plan(plan: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for item in plan[:limit]:
        exercises = []
        for exercise in item.get("ex_list") or []:
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


def _hydrate_active_proposal(saved_values: dict[str, Any]) -> dict[str, Any] | None:
    active_proposal = saved_values.get("active_proposal")
    if active_proposal:
        return active_proposal

    proposed_plan = saved_values.get("proposed_plan") or []
    proposed_plan_type = saved_values.get("proposed_plan_type")
    if not proposed_plan or proposed_plan_type not in {"workout", "diet"}:
        return None

    return {
        "domain": proposed_plan_type,
        "write_mode": "update" if saved_values.get("proposed_plan_action") == "update" else "create",
        "items": proposed_plan,
        "summary": f"{'운동' if proposed_plan_type == 'workout' else '식단'} 제안",
        "last_used_turn": int(saved_values.get("turn_count", 0) or 0),
    }


def _hydrate_recent_dialogue(saved_values: dict[str, Any]) -> dict[str, Any]:
    recent_dialogue = saved_values.get("recent_dialogue") or empty_recent_dialogue()
    recent_turns = list(recent_dialogue.get("recent_turns") or [])
    if recent_turns:
        return recent_dialogue

    messages = list(saved_values.get("messages") or [])
    if not messages:
        return empty_recent_dialogue()

    paired_turns: list[dict[str, Any]] = []
    pending_user_text: str | None = None
    turn_base = int(saved_values.get("turn_count", 0) or 0)
    for message in messages:
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
                    "user_text": pending_user_text[:320],
                    "assistant_text": content[:320],
                    "user_summary": pending_user_text[:100],
                    "assistant_summary": content[:100],
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
    return {"recent_turns": paired_turns[-4:]}


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
    display_updates = {
        **sync_proposal_fields(next_active_proposal),
        "recent_dialogue": next_recent_dialogue,
    }
    checkpoint_updates = {
        **display_updates,
        **_checkpoint_cleanup_updates(result),
    }
    await graph.aupdate_state(config, checkpoint_updates)
    return display_updates


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
    config = {"configurable": {"thread_id": session_id}}
    checkpoint_db_path = str(
        getattr(request.app.state, "checkpoint_db_path", settings.CHECKPOINT_DB_PATH)
    )
    session_lock = await _get_session_lock(session_id)
    await session_lock.acquire()
    db_session_lock = None

    try:
        try:
            db_session_lock = await acquire_session_lock(checkpoint_db_path, session_id)
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
            profile_context_changed = bool(
                req.user_profile_override
                and _profile_override_changes_plan_context(
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

        show_debug_state = settings.APP_ENV == "development" or bool(req.user_profile_override)

        background_tasks.add_task(
            update_session_activity,
            checkpoint_db_path,
            session_id,
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
            pending_writes_count=len(result.get("pending_writes") or []),
            pending_write_types=_pending_write_types(result.get("pending_writes") or []),
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
    succeeded_write_ids = list(write_result.get("succeeded_write_ids") or [])

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
    merged = dict(profile or {})
    for key, value in (changes or {}).items():
        if key in {"item_id", "plan_type", "target_dates", "_idempotency_key", "idempotency_key"}:
            continue
        merged[key] = value
    merged.setdefault("allergies", [])
    merged.setdefault("injury_history", [])
    return merged


def _merge_pending_writes(existing: list[dict[str, Any]], new_writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for write in [*(existing or []), *(new_writes or [])]:
        if not isinstance(write, dict):
            continue
        key = str(write.get("write_id") or write.get("idempotency_key") or f"{write.get('write_type')}:{repr(write.get('payload'))}")
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(write)
        normalized.setdefault("write_id", key)
        normalized.setdefault("idempotency_key", key)
        normalized.setdefault("attempt_count", 0)
        normalized.setdefault("next_retry_turn", 0)
        merged.append(normalized)
    return merged[-_MAX_PENDING_WRITES:]


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
        list(write_result.get("pending") or [])
        if write_result
        else list((state or {}).get("pending_writes") or [])
    )
    return {
        "mode": mode,
        "write_succeeded": bool(write_result.get("write_succeeded")) if write_result else None,
        "pending_count": len(pending),
        "pending_write_types": _pending_write_types(pending),
        "failed_write_types": list(write_result.get("failed_write_types") or []) if write_result else [],
        "succeeded_write_ids": list(write_result.get("succeeded_write_ids") or []) if write_result else [],
        "applied_profile": bool(write_result.get("applied_profile_changes")) if write_result else False,
    }
