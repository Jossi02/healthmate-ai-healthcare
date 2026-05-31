"""Final response gate for traceability and defensive fallbacks."""
from __future__ import annotations

import time

from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

_FALLBACK_RESPONSE = "응답을 정리하는 중 문제가 생겼어요. 요청을 한 번만 더 보내주시면 바로 이어서 도와드릴게요."
_MOJIBAKE_MARKERS = ("�", "怨", "諛", "吏", "寃", "媛", "瑜", "?대룞", "?앸", "?뚮", "?묐")


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
        if _looks_like_mojibake(response):
            repaired = _safe_response_from_state(state)
            response = repaired or _FALLBACK_RESPONSE
            source = "mojibake_guard"

        validation_report = state.get("validation_report") or {}
        deps.trace.record_current_event(
            stage="finalize",
            status="ok" if source not in {"fallback", "mojibake_guard"} else "warn",
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


def _looks_like_mojibake(text: str) -> bool:
    if not text:
        return False
    return sum(text.count(marker) for marker in _MOJIBAKE_MARKERS) >= 2


def _safe_response_from_state(state: GraphState) -> str | None:
    proposed_plan = state.get("proposed_plan") or []
    proposed_plan_type = state.get("proposed_plan_type")
    if isinstance(proposed_plan, list) and proposed_plan and proposed_plan_type in {"workout", "diet"}:
        plan_label = "식단" if proposed_plan_type == "diet" else "운동"
        preview = _safe_plan_preview(proposed_plan)
        question = f"이 {plan_label} 플랜으로 작성할까요?"
        return f"{plan_label} 플랜을 제안해요.\n{preview}\n{question}".strip()
    if state.get("needs_clarification"):
        return "운동 플랜인지 식단 플랜인지 먼저 정해주시면 바로 작성할게요."
    return None


def _safe_plan_preview(plan: list[dict]) -> str:
    lines: list[str] = []
    for item in plan[:7]:
        if not isinstance(item, dict):
            continue
        day = _safe_text(item.get("day")) or ""
        name = _safe_text(item.get("name")) or "플랜 항목"
        detail = _safe_text(item.get("detail"))
        exercises = _safe_exercise_preview(item.get("ex_list") or [])
        content = exercises or detail
        line = f"- {day} {name}".strip()
        if content:
            line = f"{line}: {content}"
        lines.append(line)
    if len(plan) > 7:
        lines.append(f"- 외 {len(plan) - 7}개 항목")
    return "\n".join(lines)


def _safe_exercise_preview(ex_list: object) -> str:
    if not isinstance(ex_list, list):
        return ""
    parts: list[str] = []
    for exercise in ex_list[:3]:
        if not isinstance(exercise, dict):
            continue
        name = _safe_text(exercise.get("exercise_name"))
        if not name:
            continue
        sets = exercise.get("sets")
        duration = exercise.get("duration_minutes")
        if isinstance(sets, int) and sets > 0:
            parts.append(f"{name} {sets}세트")
        elif isinstance(duration, int) and duration > 0:
            parts.append(f"{name} {duration}분")
        else:
            parts.append(name)
    if len(ex_list) > 3:
        parts.append(f"외 {len(ex_list) - 3}종목")
    return ", ".join(parts)


def _safe_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if _looks_like_mojibake(text) else text
