"""Helpers for bounded conversation state and turn memory."""
from __future__ import annotations

from typing import Any

from app.schemas.state import (
    ActiveProposal,
    ContextResolution,
    Domain,
    GraphState,
    RecentDialogue,
    RecentTurn,
    StateEffect,
)

RECENT_TURN_LIMIT = 4
ACTIVE_PROPOSAL_STALE_TURNS = 2

_WORKOUT_KEYWORDS = (
    "운동",
    "루틴",
    "웨이트",
    "유산소",
    "러닝",
    "걷기",
    "산책",
    "헬스",
    "트레이닝",
    "스트레칭",
    "스쿼트",
    "런지",
    "데드리프트",
    "푸시업",
    "벤치프레스",
    "풀업",
    "근육통",
    "허리",
    "무릎",
    "어깨",
    "통증",
    "휴식",
    "회복",
    "쉬어",
    "뻐근",
)
_DIET_KEYWORDS = (
    "식단",
    "식사",
    "메뉴",
    "영양",
    "칼로리",
    "다이어트",
    "간식",
    "단백질",
    "아침",
    "점심",
    "저녁",
    "식욕",
    "폭식",
    "배고",
    "먹지",
    "먹어도",
    "먹으면",
    "먹을까",
    "수분",
    "물",
)
_PROFILE_KEYWORDS = (
    "체중",
    "몸무게",
    "키",
    "알레르기",
    "부상",
    "질환",
    "약",
    "목표",
    "활동량",
    "나이",
    "성별",
    "mbti",
    "별명",
)
_CANCEL_MARKERS = ("취소", "하지 않을래", "안 할래", "이건 말고", "그건 말고")


def empty_context_resolution() -> ContextResolution:
    return {
        "resolved_reference": "none",
        "resolved_domain": "none",
        "resolved_text": "",
        "confidence": 0.0,
        "ambiguous": False,
    }


def empty_recent_dialogue() -> RecentDialogue:
    return {"recent_turns": []}


def infer_domain(text: str | None) -> Domain:
    normalized = str(text or "").lower()
    if not normalized:
        return "general"

    explicit_diet_hits = sum(
        1
        for keyword in ("식단", "식사", "메뉴", "아침", "점심", "저녁", "meal", "diet")
        if keyword in normalized
    )
    explicit_workout_hits = sum(
        1
        for keyword in ("운동", "러닝", "헬스", "근력", "유산소", "스트레칭", "산책", "웨이트", "workout", "exercise")
        if keyword in normalized
    )
    if explicit_diet_hits and explicit_workout_hits:
        return "general"
    if explicit_workout_hits:
        return "workout"
    if explicit_diet_hits:
        return "diet"

    diet_hits = sum(1 for keyword in _DIET_KEYWORDS if keyword in normalized)
    workout_hits = sum(1 for keyword in _WORKOUT_KEYWORDS if keyword in normalized)
    if diet_hits and workout_hits:
        return "general"
    if diet_hits:
        return "diet"
    if workout_hits:
        return "workout"
    if any(keyword in normalized for keyword in _PROFILE_KEYWORDS):
        return "profile"
    return "general"


def build_active_proposal(state: GraphState) -> ActiveProposal | None:
    proposed_plan = list(state.get("proposed_plan") or [])
    if not proposed_plan:
        return None

    proposed_plan_type = state.get("proposed_plan_type")
    if proposed_plan_type not in {"workout", "diet"}:
        inferred = state.get("domain")
        proposed_plan_type = inferred if inferred in {"workout", "diet"} else infer_domain(state.get("user_message"))
    if proposed_plan_type not in {"workout", "diet"}:
        proposed_plan_type = "workout"

    write_mode = "update" if state.get("proposed_plan_action") == "update" else "create"
    summary = _proposal_summary(state, proposed_plan_type, write_mode, proposed_plan)
    return {
        "domain": proposed_plan_type,
        "write_mode": write_mode,
        "items": proposed_plan,
        "summary": summary,
        "last_used_turn": int(state.get("turn_count", 0) or 0),
    }


def sync_proposal_fields(active_proposal: ActiveProposal | None) -> dict[str, Any]:
    if not active_proposal:
        return {
            "active_proposal": None,
            "awaiting_plan_confirmation": False,
            "proposed_plan": None,
            "proposed_plan_type": None,
            "proposed_plan_action": None,
        }

    return {
        "active_proposal": active_proposal,
        "awaiting_plan_confirmation": True,
        "proposed_plan": active_proposal["items"],
        "proposed_plan_type": active_proposal["domain"],
        "proposed_plan_action": active_proposal["write_mode"],
    }


def evolve_active_proposal(previous: ActiveProposal | None, state: GraphState) -> ActiveProposal | None:
    generated = build_active_proposal(state)
    if generated:
        return generated

    if not previous:
        return None

    if _is_explicit_cancel(str(state.get("user_message") or "")):
        return None

    current_turn = int(state.get("turn_count", 0) or 0)
    action_intent = state.get("action_intent")
    resolved_reference = (state.get("context_resolution") or {}).get("resolved_reference")

    if action_intent == "approval":
        return {**previous, "last_used_turn": current_turn}
    if resolved_reference == "active_proposal":
        return {**previous, "last_used_turn": current_turn}

    if current_turn - int(previous.get("last_used_turn", current_turn)) >= ACTIVE_PROPOSAL_STALE_TURNS:
        return None

    return previous


def derive_state_effect(state: GraphState) -> StateEffect:
    if state.get("needs_clarification"):
        return "clarification_requested"
    if state.get("action_intent") == "approval":
        return "proposal_approved"
    if state.get("proposed_plan"):
        if state.get("proposed_plan_action") == "update" or state.get("action_intent") == "modify":
            return "proposal_updated"
        return "proposal_created"
    if state.get("action_intent") == "record":
        if state.get("record_type") == "profile":
            return "profile_recorded"
        if state.get("record_type") == "plan_check":
            return "plan_checked"
    return "none"


def append_recent_turn(dialogue: RecentDialogue | None, turn: RecentTurn) -> RecentDialogue:
    recent_turns = list((dialogue or empty_recent_dialogue()).get("recent_turns") or [])
    recent_turns.append(turn)
    return {"recent_turns": recent_turns[-RECENT_TURN_LIMIT:]}


def build_recent_turn(state: GraphState, response_text: str) -> RecentTurn:
    resolution = state.get("context_resolution") or empty_context_resolution()
    action_intent = state.get("action_intent") or "fallback"
    domain = state.get("domain") or "general"
    support_mode = state.get("support_mode") or "normal"
    state_effect = derive_state_effect(state)
    referenced_object = resolution.get("resolved_reference") or "none"
    if action_intent == "approval" and referenced_object == "none":
        referenced_object = "active_proposal"

    return {
        "turn_id": int(state.get("turn_count", 0) or 0),
        "user_text": _truncate(str(state.get("user_message") or ""), 320),
        "assistant_text": _truncate(response_text, 320),
        "user_summary": _user_summary(state),
        "assistant_summary": _assistant_summary(action_intent, domain, support_mode, state_effect),
        "action_intent": action_intent,
        "domain": domain,
        "support_mode": support_mode,
        "referenced_object": referenced_object,
        "state_effect": state_effect,
    }


def _proposal_summary(
    state: GraphState,
    domain: str,
    write_mode: str,
    proposed_plan: list[dict[str, Any]],
) -> str:
    draft_components = state.get("draft_components") or {}
    core_message = str(draft_components.get("core_message") or "").strip()
    if core_message:
        return _truncate(core_message, 120)

    item_count = len(proposed_plan)
    plan_label = "운동" if domain == "workout" else "식단"
    action_label = "수정안" if write_mode == "update" else "생성안"
    return f"{plan_label} {action_label} {item_count}개 제안"


def _user_summary(state: GraphState) -> str:
    resolution = state.get("context_resolution") or empty_context_resolution()
    resolved_text = str(resolution.get("resolved_text") or "").strip()
    if resolved_text:
        return _truncate(resolved_text, 100)
    return _truncate(str(state.get("user_message") or ""), 100)


def _assistant_summary(
    action_intent: str,
    domain: str,
    support_mode: str,
    state_effect: StateEffect,
) -> str:
    if state_effect == "proposal_created":
        return f"{_domain_label(domain)} 생성안 제시"
    if state_effect == "proposal_updated":
        return f"{_domain_label(domain)} 수정안 제시"
    if state_effect == "proposal_approved":
        return "계획 승인 응답"
    if state_effect == "profile_recorded":
        return "프로필 변경 기록 처리"
    if state_effect == "plan_checked":
        return "오늘 계획 체크 처리"
    if state_effect == "clarification_requested":
        return "의도 확인 질문"
    if action_intent == "info":
        return f"{_domain_label(domain)} 정보 응답"
    if action_intent == "casual" and support_mode == "care":
        return "공감형 응답"
    if action_intent == "safety":
        return "안전 안내 응답"
    return "일반 응답"


def _domain_label(domain: str) -> str:
    if domain == "workout":
        return "운동"
    if domain == "diet":
        return "식단"
    if domain == "profile":
        return "프로필"
    return "일반"


def _is_explicit_cancel(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in _CANCEL_MARKERS)


def _truncate(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
