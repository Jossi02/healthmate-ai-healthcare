"""Compile user profile signals into explicit constraints for downstream nodes."""
from __future__ import annotations

import time
from typing import Any

from app.core.conversation_state import (
    merge_profile_override_for_plan_context,
    profile_change_fields,
    profile_changes_affect_plan_context,
)
from app.core.profile_constraints import build_profile_constraint_set
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState


def make_profile_constraints_node(deps: NodeDeps):
    async def profile_constraints_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        query = _resolved_query(state)
        domain = str(state.get("domain") or "general")
        profile_changes = state.get("profile_changes") or {}
        base_profile = _current_user_profile(state)
        effective_profile = _profile_with_pending_changes(base_profile, profile_changes)
        pending_overlay = _profile_with_pending_changes(_safe_dict(state.get("pending_profile_overlay")), profile_changes)
        changed_fields = profile_change_fields(profile_changes)
        has_profile_overlay = bool(pending_overlay) and bool(
            changed_fields or state.get("pending_profile_overlay")
        )
        cleared_active_proposal = profile_changes_affect_plan_context(profile_changes)
        constraints = build_profile_constraint_set(
            effective_profile,
            query,
            domain=domain,
        )

        deps.trace.record_current_event(
            stage="profile_constraints",
            status="ok",
            title="Profile constraints compiled",
            detail={
                "domain": domain,
                "constraint_count": len(constraints.get("constraints") or []),
                "hard_profile_constraints": constraints.get("hard_profile_constraints") or [],
                "request_hard_constraints": constraints.get("request_hard_constraints") or [],
                "retrieval_constraints": constraints.get("retrieval_constraints") or [],
                "query_constraints": constraints.get("query_constraints") or [],
                "critical_constraints": constraints.get("critical_constraints") or [],
                "retrieval_critical_constraints": constraints.get("retrieval_critical_constraints") or [],
                "profile_targets": constraints.get("profile_targets") or [],
                "goals": constraints.get("goals") or [],
                "should_use_rag": bool(constraints.get("should_use_rag")),
                "profile_field_coverage": constraints.get("profile_field_coverage") or {},
                "pending_profile_change_fields": sorted(changed_fields),
                "active_proposal_cleared": cleared_active_proposal,
                "pending_sequential_plan_cleared": cleared_active_proposal,
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        updates: dict[str, Any] = {
            "profile_constraints": constraints,
        }
        if has_profile_overlay:
            updates["effective_user_profile"] = effective_profile
            updates["pending_profile_overlay"] = pending_overlay
        else:
            updates["effective_user_profile"] = None
            updates["pending_profile_overlay"] = None
        if cleared_active_proposal:
            updates.update(
                {
                    "active_proposal": None,
                    "awaiting_plan_confirmation": False,
                    "proposed_plan": None,
                    "proposed_plan_type": None,
                    "proposed_plan_action": None,
                    "pending_sequential_plan": None,
                }
            )
        return updates

    return profile_constraints_node


def _resolved_query(state: GraphState) -> str:
    resolution = _safe_dict(state.get("context_resolution"))
    resolved_text = str(resolution.get("resolved_text") or "").strip()
    resolved_reference = resolution.get("resolved_reference")
    confidence = _safe_float(resolution.get("confidence"))
    if resolved_reference and resolved_reference != "none" and resolved_text and confidence >= 0.6:
        return resolved_text
    return str(state.get("user_message") or "")


def _current_user_profile(state: GraphState) -> dict[str, Any]:
    effective_profile = _safe_dict(state.get("effective_user_profile"))
    return effective_profile or _safe_dict(state.get("user_profile"))


def _profile_with_pending_changes(profile: dict[str, Any] | object, changes: object) -> dict[str, Any]:
    profile = _safe_dict(profile)
    clean_changes: dict[str, Any] = {}
    if isinstance(changes, dict):
        for key, value in changes.items():
            if key in {
                "item_id",
                "plan_type",
                "target_dates",
                "write_id",
                "idempotency_key",
                "_idempotency_key",
                "write_type",
            }:
                continue
            clean_changes[key] = value
    return merge_profile_override_for_plan_context(profile or {}, clean_changes)


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
