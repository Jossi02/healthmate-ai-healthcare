"""Context resolver for cross-turn references before intent routing."""
from __future__ import annotations

import time

from app.core.conversation_state import empty_context_resolution, infer_domain
from app.graph.deps import NodeDeps
from app.schemas.state import ContextResolution, GraphState

_REFERENCE_MARKERS = (
    "그거",
    "그걸",
    "그걸로",
    "그 계획",
    "그 식단",
    "그 운동",
    "아까",
    "방금",
    "이전",
    "그 방식",
    "그대로",
    "그럼",
    "대신",
    "오늘은",
    "시간 없",
    "버전",
    "더 줄",
)
_APPROVAL_MARKERS = ("좋아", "오케이", "진행", "적용", "반영", "확정")
_MODIFY_MARKERS = ("수정", "바꿔", "변경", "줄여", "늘려", "빼", "제외", "추가", "조정", "시간 없", "버전", "더 줄")
_QUESTION_MARKERS = ("왜", "이유", "근거", "설명", "어떻게", "해야", "해도 돼", "괜찮", "피해야")
_RECENT_CHAT_MARKERS = ("내가 뭐라고", "방금 뭐라고", "아까 뭐라고", "기억나", "기억해")


def make_context_resolver_node(deps: NodeDeps):
    async def context_resolver_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        if state.get("request_kind") == "home_recommendation":
            return {"context_resolution": empty_context_resolution()}

        resolution = _resolve_context(state)
        deps.trace.record_current_event(
            stage="context_resolver",
            status="ok",
            title="Context resolution completed",
            detail={
                "resolved_reference": resolution["resolved_reference"],
                "resolved_domain": resolution["resolved_domain"],
                "ambiguous": resolution["ambiguous"],
                "confidence": resolution["confidence"],
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return {"context_resolution": resolution}

    return context_resolver_node


def _resolve_context(state: GraphState) -> ContextResolution:
    message = str(state.get("user_message") or "").strip()
    if not message:
        return empty_context_resolution()

    normalized = message.lower()
    resolution = empty_context_resolution()

    if any(marker in normalized for marker in _RECENT_CHAT_MARKERS):
        resolution.update(
            {
                "resolved_reference": "recent_chat",
                "resolved_domain": "general",
                "resolved_text": message,
                "confidence": 0.92,
            }
        )
        return resolution

    active_proposal = _safe_dict(state.get("active_proposal"))
    if active_proposal and _looks_like_active_proposal_followup(normalized):
        domain = str(active_proposal.get("domain") or "")
        if domain not in {"workout", "diet"}:
            domain = ""
        explicit_domain = infer_domain(message)
        if explicit_domain in {"workout", "diet"} and explicit_domain != domain:
            resolution.update(
                {
                    "resolved_reference": "none",
                    "resolved_domain": explicit_domain,
                    "resolved_text": message,
                    "confidence": 0.82,
                }
            )
            return resolution
        if not domain:
            resolution.update(
                {
                    "resolved_text": message,
                    "confidence": 0.2,
                    "ambiguous": True,
                }
            )
            return resolution
        resolution.update(
            {
                "resolved_reference": "active_proposal",
                "resolved_domain": domain,
                "resolved_text": _rewrite_for_active_proposal(message, domain),
                "confidence": 0.9,
            }
        )
        return resolution

    recent_turns = _safe_list(_safe_dict(state.get("recent_dialogue")).get("recent_turns"))
    if recent_turns and any(marker in normalized for marker in _QUESTION_MARKERS):
        last_turn = _last_recent_turn(recent_turns)
        if not last_turn:
            resolution.update({"resolved_text": message, "confidence": 0.2, "ambiguous": True})
            return resolution
        resolution.update(
            {
                "resolved_reference": "previous_answer",
                "resolved_domain": _safe_domain(last_turn.get("domain")),
                "resolved_text": f"방금 응답에 대해 설명해줘: {message}",
                "confidence": 0.74,
            }
        )
        return resolution

    if recent_turns and any(marker in normalized for marker in _REFERENCE_MARKERS):
        last_turn = _last_recent_turn(recent_turns)
        if not last_turn:
            resolution.update({"resolved_text": message, "confidence": 0.2, "ambiguous": True})
            return resolution
        resolution.update(
            {
                "resolved_reference": "recent_chat",
                "resolved_domain": _safe_domain(last_turn.get("domain")),
                "resolved_text": f"최근 대화 맥락을 이어서 처리해줘: {message}",
                "confidence": 0.62,
            }
        )
        return resolution

    if any(marker in normalized for marker in _REFERENCE_MARKERS):
        resolution.update(
            {
                "resolved_text": message,
                "confidence": 0.2,
                "ambiguous": True,
            }
        )
        return resolution

    resolution["resolved_text"] = message
    return resolution


def _looks_like_active_proposal_followup(message: str) -> bool:
    if any(marker in message for marker in _REFERENCE_MARKERS):
        return True
    if any(marker in message for marker in _APPROVAL_MARKERS):
        return True
    if any(marker in message for marker in _MODIFY_MARKERS):
        return True
    return False


def _last_recent_turn(recent_turns: list[object]) -> dict | None:
    for turn in reversed(recent_turns):
        if isinstance(turn, dict):
            return turn
    return None


def _safe_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_domain(value: object) -> str:
    domain = str(value or "")
    return domain if domain in {"workout", "diet", "profile", "general"} else "general"


def _rewrite_for_active_proposal(message: str, domain: str) -> str:
    domain_label = "운동" if domain == "workout" else "식단"
    normalized = message.lower()

    if any(marker in normalized for marker in _QUESTION_MARKERS):
        return f"방금 제안한 {domain_label} 계획에 대해 설명해줘: {message}"
    if any(marker in normalized for marker in _APPROVAL_MARKERS):
        return f"방금 제안한 {domain_label} 계획을 그대로 적용해줘"
    if any(marker in normalized for marker in _MODIFY_MARKERS):
        return f"방금 제안한 {domain_label} 계획을 사용자의 요청에 맞게 수정해줘: {message}"
    return f"방금 제안한 {domain_label} 계획에 이어서 답해줘: {message}"
