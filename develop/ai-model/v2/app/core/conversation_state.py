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
PLAN_CONTEXT_PROFILE_FIELDS = {
    "age",
    "gender",
    "sex",
    "height",
    "height_cm",
    "weight",
    "body_weight",
    "current_weight",
    "bmi",
    "activity_level",
    "activityLevel",
    "exercise_level",
    "fitness_level",
    "goal",
    "primary_goal",
    "exercise_goal",
    "training_goal",
    "diet_goal",
    "diet_type",
    "dietary_preferences",
    "dietary_restrictions",
    "foods_to_avoid",
    "allergies",
    "allergy",
    "otherAllergy",
    "other_allergy",
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
    "social_orientation",
    "personality_axis",
    "personality_type",
    "personality",
    "exercise_style",
    "introversion_extroversion",
    "mbti",
    "emotional_context",
    "context_notes",
}
_PLAN_CONTEXT_PROFILE_FIELDS = PLAN_CONTEXT_PROFILE_FIELDS
PROFILE_CONTEXT_FIELD_ALIASES = {
    "activityLevel": "activity_level",
    "height_cm": "height",
    "body_weight": "weight",
    "current_weight": "weight",
    "fitness_level": "exercise_level",
    "workout_frequency": "exercise_frequency",
    "frequency_per_week": "exercise_frequency",
    "weekly_workouts": "exercise_frequency",
    "target_workouts_per_week": "exercise_frequency",
    "primary_goal": "goal",
    "exercise_goal": "goal",
    "training_goal": "goal",
    "diet_goal": "goal",
    "dietary_restrictions": "dietary_preferences",
    "foods_to_avoid": "dietary_preferences",
    "allergy": "allergies",
    "otherAllergy": "allergies",
    "other_allergy": "allergies",
    "pain_points": "injury_history",
    "medical_history": "medical_conditions",
    "conditions": "medical_conditions",
    "personality_axis": "social_orientation",
    "personality_type": "social_orientation",
    "personality": "social_orientation",
    "exercise_style": "social_orientation",
    "introversion_extroversion": "social_orientation",
}
PLAN_CONTEXT_CANONICAL_FIELDS = {
    PROFILE_CONTEXT_FIELD_ALIASES.get(field, field)
    for field in PLAN_CONTEXT_PROFILE_FIELDS
}
PLAN_CONTEXT_FIELDS_BY_CANONICAL: dict[str, set[str]] = {
    canonical: {
        field
        for field in PLAN_CONTEXT_PROFILE_FIELDS
        if PROFILE_CONTEXT_FIELD_ALIASES.get(field, field) == canonical
    }
    for canonical in PLAN_CONTEXT_CANONICAL_FIELDS
}


def canonical_profile_field(field: str) -> str:
    return PROFILE_CONTEXT_FIELD_ALIASES.get(field, field)


def canonical_profile_context(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = _safe_profile_dict(profile)
    grouped: dict[str, list[Any]] = {field: [] for field in PLAN_CONTEXT_CANONICAL_FIELDS}
    for raw_key, raw_value in profile.items():
        if raw_value in (None, "", [], {}, "[]"):
            continue
        key = canonical_profile_field(str(raw_key))
        if key in grouped:
            grouped[key].append(_canonical_profile_value(raw_value))

    canonical: dict[str, Any] = {}
    for key, values in grouped.items():
        flattened = _flatten_canonical_values(values)
        canonical[key] = flattened[0] if len(flattened) == 1 else tuple(flattened)
    return canonical


def profile_context_changed_fields(previous_profile: dict | None, next_profile: dict | None) -> list[str]:
    previous = canonical_profile_context(previous_profile)
    next_value = canonical_profile_context(next_profile)
    changed: list[str] = []
    for key in sorted(PLAN_CONTEXT_CANONICAL_FIELDS):
        if previous.get(key, "") != next_value.get(key, ""):
            changed.append(key)
    return changed


def profile_context_changed(previous_profile: dict | None, next_profile: dict | None) -> bool:
    return bool(profile_context_changed_fields(previous_profile, next_profile))


def profile_override_changes_plan_context(saved_profile: dict[str, Any], override: dict[str, Any]) -> bool:
    saved_profile = _safe_profile_dict(saved_profile)
    override = _safe_profile_dict(override)
    if not override:
        return False
    if not any(canonical_profile_field(str(key)) in PLAN_CONTEXT_CANONICAL_FIELDS for key in override):
        return False
    merged = merge_profile_override_for_plan_context(saved_profile, override)
    return profile_context_changed(saved_profile, merged)


def merge_profile_override_for_plan_context(saved_profile: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_safe_profile_dict(saved_profile))
    for raw_key, value in _safe_profile_dict(override).items():
        canonical = canonical_profile_field(str(raw_key))
        if canonical in PLAN_CONTEXT_FIELDS_BY_CANONICAL:
            for sibling_key in PLAN_CONTEXT_FIELDS_BY_CANONICAL[canonical]:
                merged.pop(sibling_key, None)
        merged[raw_key] = value
    return merged


def _safe_profile_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _canonical_profile_value(value: Any) -> Any:
    if value in (None, "", [], {}, "[]"):
        return ""
    if isinstance(value, dict):
        return tuple(
            sorted(
                f"{key}:{_canonical_profile_value(item)}"
                for key, item in value.items()
                if _canonical_profile_value(item) not in (None, "", (), [])
            )
        )
    if isinstance(value, (list, tuple, set)):
        flattened = _flatten_canonical_values(value)
        return flattened[0] if len(flattened) == 1 else tuple(flattened)
    return " ".join(str(value).strip().lower().split())


def _flatten_canonical_values(values: object) -> list[Any]:
    flattened: list[Any] = []
    source = values if isinstance(values, (list, tuple, set)) else [values]
    for value in source:
        normalized = _canonical_profile_value(value)
        if normalized in (None, "", (), []):
            continue
        if isinstance(normalized, tuple):
            flattened.extend(item for item in normalized if item not in (None, "", (), []))
        else:
            flattened.append(normalized)
    return sorted({item for item in flattened}, key=lambda item: repr(item))

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
    "밥",
    "끼니",
    "음식",
    "반찬",
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
        for keyword in ("식단", "식사", "메뉴", "밥", "끼니", "음식", "반찬", "아침", "점심", "저녁", "meal", "diet")
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
    proposed_plan = _safe_plan_items(state.get("proposed_plan"))
    if not proposed_plan:
        return None

    proposed_plan_type = state.get("proposed_plan_type")
    if proposed_plan_type not in {"workout", "diet", "bundle"}:
        if plan_items_are_mixed_domain(proposed_plan):
            proposed_plan_type = "bundle"
    if proposed_plan_type not in {"workout", "diet", "bundle"}:
        inferred = state.get("domain")
        proposed_plan_type = inferred if inferred in {"workout", "diet"} else infer_domain(state.get("user_message"))
    if proposed_plan_type not in {"workout", "diet", "bundle"}:
        proposed_plan_type = "workout"

    write_mode = "update" if state.get("proposed_plan_action") == "update" else "create"
    summary = _proposal_summary(state, proposed_plan_type, write_mode, proposed_plan)
    return {
        "domain": proposed_plan_type,
        "write_mode": write_mode,
        "items": proposed_plan,
        "summary": summary,
        "last_used_turn": _safe_int(state.get("turn_count")),
    }


def sync_proposal_fields(active_proposal: ActiveProposal | None) -> dict[str, Any]:
    if not active_proposal or active_proposal_is_mixed_domain(active_proposal):
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

    if _should_clear_active_proposal(state):
        return None

    if not previous or active_proposal_is_mixed_domain(previous):
        return None

    if _is_explicit_cancel(str(state.get("user_message") or "")):
        return None

    current_turn = _safe_int(state.get("turn_count"))
    action_intent = state.get("action_intent")
    resolved_reference = _safe_dict(state.get("context_resolution")).get("resolved_reference")

    if action_intent == "approval":
        return {**previous, "last_used_turn": current_turn}
    if resolved_reference == "active_proposal":
        return {**previous, "last_used_turn": current_turn}

    previous_turn = _safe_int(previous.get("last_used_turn"), default=current_turn)
    previous_clean = {**previous, "last_used_turn": previous_turn}
    if current_turn - previous_turn >= ACTIVE_PROPOSAL_STALE_TURNS:
        return None

    return previous_clean


def active_proposal_is_mixed_domain(active_proposal: object) -> bool:
    if not isinstance(active_proposal, dict):
        return False
    if active_proposal.get("domain") == "bundle":
        return False
    return plan_items_are_mixed_domain(active_proposal.get("items") or [])


def plan_items_are_mixed_domain(items: object) -> bool:
    if not isinstance(items, list):
        return False
    domains = {_infer_plan_item_domain(item) for item in items if isinstance(item, dict)}
    domains.discard(None)
    return "workout" in domains and "diet" in domains


def _infer_plan_item_domain(item: dict[str, Any]) -> str | None:
    explicit_type = str(item.get("plan_type") or item.get("type") or "").strip().lower()
    if explicit_type in {"workout", "exercise", "training", "routine"}:
        return "workout"
    if explicit_type in {"diet", "meal", "food", "menu", "nutrition"}:
        return "diet"
    if item.get("ex_list") or item.get("exercises") or item.get("exercise_name"):
        return "workout"
    if item.get("food_name") or item.get("meal_name") or item.get("foods"):
        return "diet"
    item_type = str(item.get("type") or item.get("plan_type") or item.get("category") or "").strip().lower()
    if item_type in {"exercise", "workout", "training", "routine"}:
        return "workout"
    if item_type in {"meal", "diet", "food", "menu", "nutrition"}:
        return "diet"
    text = " ".join(
        str(value)
        for value in (
            item.get("name"),
            item.get("detail"),
            item.get("summary"),
            item.get("category"),
            item.get("slot"),
            item.get("meal_type"),
        )
        if value
    ).lower()
    if any(marker in text for marker in ("breakfast", "lunch", "dinner", "snack", "meal", "food", "diet", "menu", "oat", "yogurt", "rice", "salad", "chicken", "salmon")):
        return "diet"
    if any(marker in text for marker in ("workout", "exercise", "routine", "sets", "reps", "cardio", "stretch", "strength", "squat", "press", "walk", "run")):
        return "workout"
    inferred = infer_domain(text)
    if inferred in {"workout", "diet"}:
        return inferred
    return None


def _should_clear_active_proposal(state: GraphState) -> bool:
    if state.get("record_type") == "plan_delete":
        return True

    if _profile_changes_affect_active_proposal(state.get("profile_changes")):
        return True

    report = state.get("validation_report") or {}
    if isinstance(report, dict) and report.get("passed") is False:
        issues = report.get("issues") or []
        if any(isinstance(issue, dict) and issue.get("severity") == "critical" for issue in issues):
            return True

    return bool(
        state.get("needs_clarification")
        and state.get("action_intent") in {"create", "modify"}
        and not state.get("proposed_plan")
    )


def _profile_changes_affect_active_proposal(changes: object) -> bool:
    return bool(_profile_change_fields(changes) & PLAN_CONTEXT_CANONICAL_FIELDS)


def profile_changes_affect_plan_context(changes: object) -> bool:
    return _profile_changes_affect_active_proposal(changes)


def profile_change_fields(changes: object) -> set[str]:
    return _profile_change_fields(changes)


def _profile_change_fields(changes: object) -> set[str]:
    fields: set[str] = set()
    if isinstance(changes, dict):
        field = changes.get("field") or changes.get("key") or changes.get("name")
        if isinstance(field, str):
            fields.add(canonical_profile_field(field))
        for key, value in changes.items():
            if isinstance(key, str) and canonical_profile_field(key) in PLAN_CONTEXT_CANONICAL_FIELDS:
                fields.add(canonical_profile_field(key))
            fields.update(_profile_change_fields(value))
    elif isinstance(changes, list):
        for item in changes:
            fields.update(_profile_change_fields(item))
    elif isinstance(changes, str) and canonical_profile_field(changes) in PLAN_CONTEXT_CANONICAL_FIELDS:
        fields.add(canonical_profile_field(changes))
    return fields


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
        if state.get("record_type") == "plan_delete":
            return "plan_deleted"
    return "none"


def append_recent_turn(dialogue: RecentDialogue | None, turn: RecentTurn) -> RecentDialogue:
    recent_turns = _safe_list(_safe_dict(dialogue).get("recent_turns"))
    if isinstance(turn, dict):
        recent_turns.append(turn)
    return {"recent_turns": recent_turns[-RECENT_TURN_LIMIT:]}


def build_recent_turn(state: GraphState, response_text: str) -> RecentTurn:
    resolution = _safe_dict(state.get("context_resolution")) or empty_context_resolution()
    action_intent = state.get("action_intent") or "fallback"
    domain = state.get("domain") or "general"
    support_mode = state.get("support_mode") or "normal"
    state_effect = derive_state_effect(state)
    referenced_object = resolution.get("resolved_reference") or "none"
    if action_intent == "approval" and referenced_object == "none":
        referenced_object = "active_proposal"

    return {
        "turn_id": _safe_int(state.get("turn_count")),
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
    draft_components = _safe_dict(state.get("draft_components"))
    core_message = str(draft_components.get("core_message") or "").strip()
    if core_message:
        return _truncate(core_message, 120)

    item_count = len(proposed_plan)
    plan_label = "운동" if domain == "workout" else "식단"
    action_label = "수정안" if write_mode == "update" else "생성안"
    return f"{plan_label} {action_label} {item_count}개 제안"


def _user_summary(state: GraphState) -> str:
    resolution = _safe_dict(state.get("context_resolution")) or empty_context_resolution()
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
    if state_effect == "plan_deleted":
        return "계획 삭제 처리"
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


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_plan_items(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in _safe_list(value) if isinstance(item, dict)][:80]


def _safe_int(value: object, *, default: int = 0) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _is_explicit_cancel(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in _CANCEL_MARKERS)


def _truncate(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
