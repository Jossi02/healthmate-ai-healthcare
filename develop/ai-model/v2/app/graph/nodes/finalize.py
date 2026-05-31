"""Final response gate for traceability and defensive fallbacks."""
from __future__ import annotations

import time

from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

_FALLBACK_RESPONSE = "응답을 정리하는 중 문제가 생겼어요. 요청을 한 번만 더 보내주시면 바로 이어서 도와드릴게요."


def make_finalize_node(deps: NodeDeps):
    async def finalize_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        if state.get("request_kind") == "home_recommendation":
            recommendations = state.get("home_recommendations") or {}
            deps.trace.record_current_event(
                stage="finalize",
                status="ok",
                title="Home recommendations finalized",
                detail={
                    "has_home_recommendations": bool(recommendations),
                    "resolved_persona_id": state.get("resolved_persona_id"),
                },
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {"force_regenerate": False}

        response = str(state.get("response") or "").strip()
        source = "response"

        if not response and state.get("draft_components"):
            response = render_draft_preview(normalize_draft_components(state.get("draft_components")))
            source = "draft_components"
        if not response and state.get("draft_response"):
            response = str(state.get("draft_response") or "").strip()
            source = "draft_response"
        if not response:
            response = _FALLBACK_RESPONSE
            source = "fallback"

        validation_report = state.get("validation_report") or {}
        deps.trace.record_current_event(
            stage="finalize",
            status="ok" if source != "fallback" else "warn",
            title="Final response prepared",
            detail={
                "source": source,
                "response_length": len(response),
                "validation_passed": validation_report.get("passed"),
                "validation_issue_count": len(validation_report.get("issues") or []),
                "resolved_persona_id": state.get("resolved_persona_id"),
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )

        return {
            "response": response,
            "self_eval_failure_reason": None,
        }

    return finalize_node
