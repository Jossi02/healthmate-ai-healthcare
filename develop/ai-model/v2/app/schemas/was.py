"""WAS request/response schemas and payload normalization helpers."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")
_SETS_PATTERN = re.compile(r"(\d+)")
_KST = ZoneInfo("Asia/Seoul")
_MEAL_KEYWORDS = {
    "breakfast",
    "lunch",
    "dinner",
    "snack",
    "brunch",
    "meal",
    "아침",
    "점심",
    "저녁",
    "간식",
    "야식",
    "식단",
    "식사",
}
_WORKOUT_KEYWORDS = {
    "workout",
    "exercise",
    "cardio",
    "strength",
    "stretch",
    "session",
    "routine",
    "운동",
    "유산소",
    "근력",
    "스트레칭",
    "루틴",
}
_WEEKDAY_KEYWORDS = {
    "월요일": 0,
    "화요일": 1,
    "수요일": 2,
    "목요일": 3,
    "금요일": 4,
    "토요일": 5,
    "일요일": 6,
}


class WASUserProfile(BaseModel):
    """GET /api/user/profile/{user_id} response."""

    user_id: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    diet_type: Optional[str] = None
    allergies: Optional[list[str]] = None
    injury_history: Optional[list[str]] = None
    goal: Optional[str] = None
    activity_level: Optional[str] = None
    selected_ai_persona: Optional[str] = None

    model_config = {"extra": "allow"}


class WASExerciseItem(BaseModel):
    exercise_name: str
    sets: Optional[int] = None
    duration_minutes: Optional[int] = None
    calories: int = 0


class WASPlanItem(BaseModel):
    """A single workout/meal plan item."""

    id: Optional[str] = None
    name: str
    detail: Optional[str] = None
    day: Optional[str] = None
    ex_list: list[WASExerciseItem] = Field(default_factory=list)
    completed: bool = False

    model_config = {"extra": "allow"}


class WASTodayPlan(BaseModel):
    """GET /api/plan/today/{user_id} response wrapper."""

    items: list[WASPlanItem] = Field(default_factory=list)


class WASProfileUpdateRequest(BaseModel):
    """PUT /api/user/profile/{user_id} request body."""

    weight: Optional[float] = None
    height: Optional[float] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    diet_type: Optional[str] = None
    allergies: Optional[list[str]] = None
    injury_history: Optional[list[str]] = None
    goal: Optional[str] = None
    activity_level: Optional[str] = None

    model_config = {"extra": "forbid"}


class WASPlanCreateRequest(BaseModel):
    """POST /api/plan/create/{user_id} request body."""

    plan_type: str
    items: list[WASPlanItem]

    model_config = {"extra": "forbid"}


class WASPlanUpdateRequest(BaseModel):
    """PUT /api/plan/update/{user_id} request body."""

    plan_type: str
    items: list[WASPlanItem]

    model_config = {"extra": "forbid"}


class WASPlanCheckRequest(BaseModel):
    """PUT /api/plan/check/{user_id} request body."""

    item_id: str

    model_config = {"extra": "forbid"}


def to_profile_update(changes: dict[str, Any]) -> dict[str, Any]:
    """Convert profile_changes into a validated WAS payload."""

    req = WASProfileUpdateRequest.model_validate(changes)
    return req.model_dump(exclude_none=True)


def to_plan_create(extracted: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a proposed new plan into the WAS create payload."""

    payloads = to_plan_create_batches(extracted)
    if not payloads:
        return None

    if len(payloads) > 1:
        logger.warning("Plan create normalization produced multiple payloads; use to_plan_create_batches")
        return None

    return payloads[0]


def to_plan_update(extracted: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a proposed updated plan into the WAS update payload."""

    payloads = to_plan_update_batches(extracted)
    if not payloads:
        return None

    if len(payloads) > 1:
        logger.warning("Plan update normalization produced multiple payloads; use to_plan_update_batches")
        return None

    return payloads[0]


def to_plan_check(item_id: str) -> dict[str, Any]:
    """Convert item_id into the WAS check payload."""

    req = WASPlanCheckRequest(item_id=item_id)
    return req.model_dump()


def to_plan_create_batches(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a proposed new plan into one or more WAS create payloads."""

    return _build_plan_payload_batches(extracted, update_mode=False)


def to_plan_update_batches(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a proposed updated plan into one or more WAS update payloads."""

    return _build_plan_payload_batches(extracted, update_mode=True)


def _normalize_plan_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "diet":
        return "diet"
    return "workout"


def _normalize_plan_items(items: Any, plan_type: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        normalized_item = _normalize_plan_item(item, plan_type)
        if normalized_item:
            normalized.append(normalized_item)
        else:
            logger.warning("Dropped invalid proposed plan item at index=%d", index)
    return normalized


def _build_plan_payload_batches(extracted: dict[str, Any], *, update_mode: bool) -> list[dict[str, Any]]:
    required_flag = "has_changes" if update_mode else "has_plan"
    if not extracted.get(required_flag) or not extracted.get("items"):
        return []

    plan_type = _normalize_plan_type(extracted.get("plan_type"))
    grouped_items = _normalize_plan_items_by_type(extracted.get("items"), plan_type)
    if not grouped_items:
        logger.warning(
            "Plan %s normalization produced no valid items",
            "update" if update_mode else "create",
        )
        return []

    request_type = WASPlanUpdateRequest if update_mode else WASPlanCreateRequest
    payloads: list[dict[str, Any]] = []
    for grouped_plan_type, items in grouped_items:
        req = request_type(plan_type=grouped_plan_type, items=items)
        payloads.append(
            req.model_dump(
                exclude_none=True,
                exclude={"items": {"__all__": {"id", "completed"}}},
            )
        )
    return payloads


def _normalize_plan_items_by_type(items: Any, default_plan_type: str) -> list[tuple[str, list[dict[str, Any]]]]:
    if not isinstance(items, list):
        return []

    grouped: dict[str, list[dict[str, Any]]] = {"workout": [], "diet": []}
    ordered_types: list[str] = []

    for index, item in enumerate(items):
        plan_type = _infer_item_plan_type(item, default_plan_type)
        normalized_item = _normalize_plan_item(item, plan_type)
        if not normalized_item:
            logger.warning("Dropped invalid proposed plan item at index=%d", index)
            continue

        if plan_type not in ordered_types:
            ordered_types.append(plan_type)
        grouped[plan_type].append(normalized_item)

    return [
        (plan_type, grouped[plan_type])
        for plan_type in ordered_types
        if grouped[plan_type]
    ]


def _normalize_plan_item(item: Any, plan_type: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    name = _first_non_empty(
        item.get("name"),
        item.get("title"),
        item.get("meal_name"),
        item.get("category"),
        default="운동 계획" if plan_type == "workout" else "식단 계획",
    )
    detail = _first_non_empty(
        item.get("detail"),
        item.get("description"),
        item.get("summary"),
        default=None,
    )
    if plan_type == "diet":
        detail = _clean_diet_plan_detail(detail)
    day = _normalize_day(
        item.get("day") or item.get("date"),
        name=name,
        detail=detail,
    )

    raw_ex_list = item.get("ex_list")
    if raw_ex_list is None:
        raw_ex_list = item.get("exercise_list")
    if raw_ex_list is None:
        raw_ex_list = item.get("exercises")

    ex_list = [] if plan_type == "diet" else _normalize_exercises(raw_ex_list)
    if plan_type == "workout":
        name = _normalize_workout_category_name(name, detail, ex_list)

    normalized = WASPlanItem(
        name=name,
        detail=detail,
        day=day,
        ex_list=ex_list,
    )
    return normalized.model_dump(exclude_none=True)


_DIET_DETAIL_EXPLANATION_MARKERS = (
    "알레르기",
    "식이 제약",
    "질환",
    "고려",
    "제외",
    "대체",
    "목표",
    "제약",
    "반영",
    "프로필",
    "위험",
    "안전",
    "allergy",
    "constraint",
    "goal",
    "profile",
    "because",
    "avoid",
    "replace",
)
_DIET_DETAIL_SPLIT_RE = re.compile(r"\s*(?:/|;|\||\n|•|·|\s+-\s+|(?<=[.!?。])\s+)\s*")
_DIET_DETAIL_BRACKET_RE = re.compile(
    r"\s*[\(\[][^\)\]]*(?:"
    + "|".join(re.escape(marker) for marker in _DIET_DETAIL_EXPLANATION_MARKERS)
    + r")[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_DIET_RATIONALE_PREFIX_MARKERS = (
    "혈당",
    "감량",
    "증량",
    "근육",
    "체중",
    "칼로리",
    "안정",
    "회복",
    "질환",
    "부상",
    "rationale",
)


def _clean_diet_plan_detail(detail: str | None) -> str | None:
    text = str(detail or "").strip()
    if not text:
        return detail
    text = re.sub(r"\s+", " ", _DIET_DETAIL_BRACKET_RE.sub("", text)).strip()
    pieces = [piece.strip() for piece in _DIET_DETAIL_SPLIT_RE.split(text) if piece.strip()]
    concrete = [_clean_diet_detail_piece(piece) for piece in pieces]
    concrete = [piece for piece in concrete if piece and not _is_explanatory_diet_piece(piece)]
    if not concrete and pieces:
        concrete = [_clean_diet_detail_piece(pieces[0])]
    return _bound_diet_detail(" / ".join(piece for piece in concrete if piece).strip()) or None


def _clean_diet_detail_piece(piece: str) -> str:
    text = _DIET_DETAIL_BRACKET_RE.sub("", str(piece or "")).strip()
    if not text:
        return ""

    lowered = text.lower()
    marker_positions = [
        lowered.find(marker.lower())
        for marker in _DIET_DETAIL_EXPLANATION_MARKERS
        if marker.lower() in lowered
    ]
    if marker_positions:
        marker_index = min(position for position in marker_positions if position >= 0)
        if marker_index <= 0:
            return ""
        prefix = text[:marker_index].rstrip(" -:,.()[]")
        if _looks_like_diet_rationale_prefix(prefix):
            return ""
        text = prefix

    text = re.sub(r"\s*(?:때문에|위해서|위해|맞춰|반영해|반영하여).*$", "", text).strip()
    return text.strip(" -:,.")


def _looks_like_diet_rationale_prefix(prefix: str) -> bool:
    text = str(prefix or "").strip().lower()
    if not text:
        return True
    if len(text) <= 12 and any(marker in text for marker in _DIET_RATIONALE_PREFIX_MARKERS):
        return True
    return False


def _is_explanatory_diet_piece(piece: str) -> bool:
    lowered = str(piece or "").lower()
    return any(marker.lower() in lowered for marker in _DIET_DETAIL_EXPLANATION_MARKERS)


def _bound_diet_detail(detail: str) -> str:
    text = re.sub(r"\s+", " ", str(detail or "")).strip()
    if len(text) <= 80:
        return text

    parts = [
        part.strip()
        for part in re.split(r"\s*(?:,|/|\+|와|과)\s*", text)
        if part.strip()
    ]
    if len(parts) >= 2:
        text = ", ".join(parts[:3]).strip()
    return text[:80].rstrip(" ,/+")


def _normalize_workout_category_name(
    name: str | None,
    detail: str | None,
    ex_list: list[dict[str, Any]],
) -> str:
    current = str(name or "운동 계획").strip() or "운동 계획"
    text = " ".join(
        [
            current,
            str(detail or ""),
            " ".join(str(item.get("exercise_name") or "") for item in ex_list if isinstance(item, dict)),
        ]
    ).lower()

    strong_stretching = any(
        marker in text
        for marker in ("스트레칭", "stretch", "요가", "이완", "mobility", "가동성", "폼롤")
    )
    if strong_stretching:
        return "스트레칭 루틴"

    if any(marker in text for marker in ("upper_body", "상체", "푸시업", "푸쉬업", "로우", "가슴", "등", "어깨")):
        return "상체 루틴"
    if any(marker in text for marker in ("lower_body", "하체", "스쿼트", "런지", "브릿지", "둔근")):
        return "하체 루틴"
    if any(marker in text for marker in ("cardio", "유산소", "걷기", "러닝", "달리기", "자전거", "사이클")):
        return "유산소 루틴"
    return current


def _infer_item_plan_type(item: Any, default_plan_type: str) -> str:
    if not isinstance(item, dict):
        return default_plan_type

    name = _first_non_empty(
        item.get("name"),
        item.get("title"),
        item.get("meal_name"),
        item.get("category"),
        default="",
    ) or ""
    detail = _first_non_empty(
        item.get("detail"),
        item.get("description"),
        item.get("summary"),
        default="",
    ) or ""
    combined = f"{name} {detail}".strip().lower()

    if any(keyword in combined for keyword in _MEAL_KEYWORDS):
        return "diet"

    if any(keyword in combined for keyword in _WORKOUT_KEYWORDS):
        return "workout"

    raw_ex_list = item.get("ex_list")
    if raw_ex_list is None:
        raw_ex_list = item.get("exercise_list")
    if raw_ex_list is None:
        raw_ex_list = item.get("exercises")

    if _normalize_exercises(raw_ex_list):
        return "workout"

    return default_plan_type


def _normalize_exercises(raw_exercises: Any) -> list[dict[str, Any]]:
    if raw_exercises is None:
        return []

    if isinstance(raw_exercises, dict):
        raw_items = [raw_exercises]
    elif isinstance(raw_exercises, list):
        raw_items = raw_exercises
    elif isinstance(raw_exercises, str):
        raw_items = [{"exercise_name": raw_exercises, "sets": 3, "calories": 0}]
    else:
        return []

    normalized: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, str):
            exercise_name = raw_item.strip()
            sets = 3
            duration_minutes = None
            calories = 0
        elif isinstance(raw_item, dict):
            exercise_name = _first_non_empty(
                raw_item.get("exercise_name"),
                raw_item.get("name"),
                raw_item.get("exercise"),
                raw_item.get("title"),
                default="",
            )
            sets = _normalize_sets(
                raw_item.get("sets", raw_item.get("set", raw_item.get("count")))
            )
            duration_minutes = _normalize_optional_int(
                raw_item.get("duration_minutes", raw_item.get("duration", raw_item.get("minutes")))
            )
            calories = _normalize_optional_int(raw_item.get("calories")) or 0
        else:
            continue

        if not exercise_name:
            continue

        if duration_minutes is not None:
            sets = None

        normalized.append(
            WASExerciseItem(
                exercise_name=exercise_name,
                sets=sets,
                duration_minutes=duration_minutes,
                calories=calories,
            ).model_dump(exclude_none=True)
        )
    return normalized


def _normalize_day(
    value: Any,
    *,
    name: str | None = None,
    detail: str | None = None,
) -> Optional[str]:
    if value is None:
        return _infer_day_from_text(name, detail)

    text = str(value).strip()
    if not text:
        return _infer_day_from_text(name, detail)

    for fmt in _DATE_INPUT_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            if parsed.year < datetime.now(_KST).year:
                inferred = _infer_day_from_text(name, detail)
                if inferred:
                    return inferred
            return parsed.isoformat()
        except ValueError:
            continue
    return _infer_day_from_text(name, detail) or text


def _infer_day_from_text(name: str | None, detail: str | None) -> Optional[str]:
    combined = " ".join(part for part in (name, detail) if part).strip()
    if not combined:
        return None

    today = datetime.now(_KST).date()
    week_start = today - timedelta(days=today.weekday())
    next_week_start = week_start + timedelta(days=7)
    use_next_week = "다음 주" in combined or "다음주" in combined
    base_week = next_week_start if use_next_week else week_start

    for keyword, weekday_index in _WEEKDAY_KEYWORDS.items():
        if keyword in combined:
            return (base_week + timedelta(days=weekday_index)).isoformat()

    if "오늘" in combined:
        return today.isoformat()
    if "내일" in combined:
        return (today + timedelta(days=1)).isoformat()

    return None


def _normalize_sets(value: Any) -> int:
    if value is None:
        return 3
    if isinstance(value, bool):
        return 3
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, float):
        return max(1, int(value))

    match = _SETS_PATTERN.search(str(value))
    if match:
        return max(1, int(match.group(1)))
    return 3


def _normalize_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    match = _SETS_PATTERN.search(str(value))
    if match:
        return int(match.group(1))
    return None


def _first_non_empty(*values: Any, default: Optional[str] = None) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default
