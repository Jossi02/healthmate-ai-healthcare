"""Record node for profile updates and today-plan check completion."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.exceptions import ExternalServiceError
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)

_ALLOWED_PROFILE_FIELDS = {
    "weight",
    "height",
    "diet_type",
    "allergies",
    "injury_history",
    "goal",
    "activity_level",
    "age",
    "gender",
    "mbti",
}

_ERR_INVALID_FIELD = (
    "지원되지 않는 프로필 항목이에요. 수정 가능한 항목은 체중, 키, 식단유형, "
    "알레르기, 부상 이력, 목표, 활동량, 나이, 성별, MBTI예요."
)
_ERR_NOT_TODAY = "오늘 계획만 기록할 수 있어요."
_ERR_NOT_IN_PLAN = "오늘 계획에 없는 항목이에요."
_ERR_DELETE_DATE = "삭제할 날짜를 찾지 못했어요. 예: 오늘 운동 플랜 삭제해줘"
_KST = ZoneInfo("Asia/Seoul")


def make_record_node(deps: NodeDeps):
    async def record_node(state: GraphState) -> dict:
        record_type = state.get("record_type")

        if record_type == "profile":
            return await _handle_profile(state)
        if record_type == "plan_check":
            return await _handle_plan_check(deps, state)
        if record_type == "plan_delete":
            return await _handle_plan_delete(state)

        logger.warning("record_type missing; returning empty update")
        return {}

    return record_node


async def _handle_profile(state: GraphState) -> dict:
    changes: dict[str, Any] = state.get("profile_changes") or _infer_profile_changes(
        str(state.get("user_message") or "")
    )

    invalid_fields = set(changes.keys()) - _ALLOWED_PROFILE_FIELDS
    if invalid_fields:
        logger.info("Unsupported profile fields detected: %s", sorted(invalid_fields))
        return {"response": _ERR_INVALID_FIELD}

    current_profile = dict(state.get("effective_user_profile") or state.get("user_profile") or {})
    updated_profile = {**current_profile, **changes}
    pending_overlay = {
        **(state.get("pending_profile_overlay") or {}),
        **changes,
    }

    return {
        "effective_user_profile": updated_profile,
        "pending_profile_overlay": pending_overlay,
        "profile_changes": changes,
    }


async def _handle_plan_check(deps: NodeDeps, state: GraphState) -> dict:
    if not state.get("is_today", False):
        return {"response": _ERR_NOT_TODAY}

    profile_changes = state.get("profile_changes") or {}

    today_plan: list[dict] = state.get("today_plan") or []
    try:
        today_plan = await deps.was.get_today_plan(state["user_id"])
    except ExternalServiceError as exc:
        logger.warning("plan_check refresh failed; using cached today_plan: %s", exc)

    item_id = profile_changes.get("item_id") or _infer_plan_check_item_id(
        str(state.get("user_message") or ""),
        today_plan,
    )
    plan_ids = {item.get("id") for item in today_plan}
    if item_id not in plan_ids:
        return {"response": _ERR_NOT_IN_PLAN}

    return {
        "profile_changes": {"item_id": item_id},
    }


async def _handle_plan_delete(state: GraphState) -> dict:
    payload = state.get("profile_changes") or _infer_plan_delete_payload(
        str(state.get("user_message") or "")
    )
    target_dates = payload.get("target_dates") or []
    plan_type = payload.get("plan_type") or "all"

    if not target_dates:
        return {"response": _ERR_DELETE_DATE}

    return {
        "profile_changes": {
            "plan_type": plan_type,
            "target_dates": target_dates,
        },
    }


def _infer_profile_changes(message: str) -> dict[str, Any]:
    normalized = " ".join(message.strip().split())
    lowered = normalized.lower()
    changes: dict[str, Any] = {}

    weight_match = re.search(r"(\d+(?:\.\d+)?)\s*kg", lowered)
    if weight_match and any(token in lowered for token in ("체중", "몸무게")):
        changes["weight"] = float(weight_match.group(1))

    height_match = re.search(r"(\d+(?:\.\d+)?)\s*cm", lowered)
    if height_match and "키" in lowered:
        changes["height"] = float(height_match.group(1))

    age_match = re.search(r"(\d+)\s*살", lowered)
    if age_match and "나이" in lowered:
        changes["age"] = int(age_match.group(1))

    allergy_match = re.search(r"([가-힣a-z0-9\s]+?)\s*알레르기", normalized, re.IGNORECASE)
    if allergy_match:
        allergy = allergy_match.group(1).strip()
        allergy = re.sub(r"^(내|저|제)\s+", "", allergy).strip()
        allergy = re.sub(r"\s*(추가|기록|저장|반영|업데이트|변경|수정)해줘?$", "", allergy).strip()
        if allergy and allergy != "알레르기":
            changes["allergies"] = [allergy]

    goal_match = re.search(
        r"목표(?:를|는|가)?\s*([가-힣a-z0-9\s]+?)\s*(?:으로|로)\s*(?:변경|수정|기록|저장|반영|업데이트)",
        normalized,
        re.IGNORECASE,
    )
    if goal_match:
        goal = goal_match.group(1).strip()
        if goal:
            changes["goal"] = goal

    return changes


def _infer_plan_delete_payload(message: str) -> dict[str, Any]:
    return {
        "plan_type": _infer_plan_delete_type(message),
        "target_dates": _infer_plan_delete_dates(message),
    }


def _infer_plan_delete_type(message: str) -> str:
    lowered = message.lower()
    has_workout = any(
        token in lowered
        for token in ("운동", "루틴", "헬스", "근력", "유산소", "workout", "exercise")
    )
    has_diet = any(
        token in lowered
        for token in ("식단", "식사", "메뉴", "아침", "점심", "저녁", "diet", "meal")
    )
    if has_workout and not has_diet:
        return "workout"
    if has_diet and not has_workout:
        return "diet"
    return "all"


def _infer_plan_delete_dates(message: str) -> list[str]:
    normalized = " ".join(message.strip().split())
    today = datetime.now(_KST).date()
    explicit_dates = _extract_explicit_dates(normalized, today.year)
    if explicit_dates:
        return explicit_dates

    lowered = normalized.lower()
    if any(token in lowered for token in ("일주일", "1주", "7일", "이번 주", "이번주", "주간")):
        return [(today + timedelta(days=offset)).isoformat() for offset in range(7)]
    if any(token in lowered for token in ("한 달", "한달", "1달", "1개월", "30일", "월간")):
        return [(today + timedelta(days=offset)).isoformat() for offset in range(30)]
    if "내일" in lowered:
        return [(today + timedelta(days=1)).isoformat()]
    if "어제" in lowered:
        return [(today - timedelta(days=1)).isoformat()]

    return [today.isoformat()]


def _extract_explicit_dates(message: str, current_year: int) -> list[str]:
    dates: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", message):
        parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed and parsed.isoformat() not in seen:
            seen.add(parsed.isoformat())
            dates.append(parsed.isoformat())

    for match in re.finditer(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", message):
        parsed = _safe_date(current_year, int(match.group(1)), int(match.group(2)))
        if parsed and parsed.isoformat() not in seen:
            seen.add(parsed.isoformat())
            dates.append(parsed.isoformat())

    return dates


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _today_iso() -> str:
    return datetime.now(_KST).date().isoformat()


def _matches_delete_plan_type(item: dict, plan_type: str) -> bool:
    if plan_type == "all":
        return True
    item_type = str(item.get("type") or "").lower()
    if plan_type == "workout":
        return item_type == "exercise"
    if plan_type == "diet":
        return item_type == "meal"
    return False


def _infer_plan_check_item_id(message: str, today_plan: list[dict]) -> str | None:
    if not today_plan:
        return None

    lowered = message.lower()
    target_type = None
    if any(token in lowered for token in ("식단", "식사", "메뉴", "먹었")):
        target_type = "meal"
    elif any(token in lowered for token in ("운동", "루틴", "세트", "유산소")):
        target_type = "exercise"

    if target_type:
        typed_candidates = [
            item for item in today_plan if str(item.get("type") or "").lower() == target_type
        ]
        incomplete_typed = [item for item in typed_candidates if not item.get("completed")]
        if incomplete_typed:
            return str(incomplete_typed[0].get("id") or "") or None
        if typed_candidates:
            return str(typed_candidates[0].get("id") or "") or None

    incomplete = [item for item in today_plan if not item.get("completed")]
    if incomplete:
        return str(incomplete[0].get("id") or "") or None
    return str(today_plan[0].get("id") or "") or None
