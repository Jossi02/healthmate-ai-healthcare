"""Final response gate for traceability and defensive fallbacks."""
from __future__ import annotations

import time
from typing import Any

from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

_FALLBACK_RESPONSE = "응답을 정리하는 중 문제가 생겼어요. 요청을 한 번만 더 보내주시면 바로 이어서 도와드릴게요."
_MOJIBAKE_MARKERS = ("�", "怨", "諛", "吏", "寃", "媛", "瑜", "?대룞", "?앸", "?뚮", "?묐")


def make_finalize_node(deps: NodeDeps):
    async def finalize_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        if state.get("request_kind") == "home_recommendation":
            recommendations = _safe_dict(state.get("home_recommendations"))
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

        response = _response_text(state.get("response"))
        source = "response"

        draft_components = _safe_dict(state.get("draft_components"))
        if not response and draft_components:
            response = render_draft_preview(normalize_draft_components(draft_components))
            source = "draft_components"
        if not response and state.get("draft_response"):
            response = _response_text(state.get("draft_response"))
            source = "draft_response"
        if not response:
            response = _FALLBACK_RESPONSE
            source = "fallback"
        if _looks_like_mojibake(response):
            repaired = _safe_response_from_state(state)
            response = repaired or _FALLBACK_RESPONSE
            source = "mojibake_guard"

        validation_report = _safe_dict(state.get("validation_report"))
        validation_issues = _safe_list(validation_report.get("issues"))
        deps.trace.record_current_event(
            stage="finalize",
            status="ok" if source not in {"fallback", "mojibake_guard"} else "warn",
            title="Final response prepared",
            detail={
                "source": source,
                "response_length": len(response),
                "validation_passed": validation_report.get("passed"),
                "validation_issue_count": len(validation_issues),
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
    proposed_plan = _safe_list(state.get("proposed_plan"))
    proposed_plan_type = state.get("proposed_plan_type")
    if proposed_plan and proposed_plan_type in {"workout", "diet"}:
        plan_label = "식단" if proposed_plan_type == "diet" else "운동"
        preview = _safe_plan_preview(proposed_plan)
        if not preview:
            return None
        question = f"이 {plan_label} 플랜으로 작성할까요?"
        return f"{plan_label} 플랜을 제안해요.\n{preview}\n{question}".strip()
    if state.get("needs_clarification"):
        return "운동 플랜인지 식단 플랜인지 먼저 정해주시면 바로 작성할게요."
    return None


def _safe_plan_preview(plan: object) -> str:
    items = [item for item in _safe_list(plan) if isinstance(item, dict)]
    lines: list[str] = []
    for item in items[:7]:
        day = _safe_text(item.get("day")) or ""
        name = _safe_text(item.get("name")) or "플랜 항목"
        detail = _safe_text(item.get("detail"))
        exercises = _safe_exercise_preview(item.get("ex_list") or [])
        content = exercises or detail
        line = f"- {day} {name}".strip()
        if content:
            line = f"{line}: {content}"
        lines.append(line)
    if len(items) > 7:
        lines.append(f"- 외 {len(items) - 7}개 항목")
    return "\n".join(lines)


def _safe_exercise_preview(ex_list: object) -> str:
    items = [item for item in _safe_list(ex_list) if isinstance(item, dict)]
    parts: list[str] = []
    for exercise in items[:3]:
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
    if len(items) > 3:
        parts.append(f"외 {len(items) - 3}종목")
    return ", ".join(parts)


def _safe_text(value: object) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    text = str(value or "").strip()
    return "" if _looks_like_mojibake(text) else text


def _response_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
