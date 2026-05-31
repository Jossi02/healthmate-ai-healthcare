"""Shared helpers for home-tab recommendation generation."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.diet_safety_rules import (
    COMMON_EATING_DISORDER_RISK_TERMS,
    COMMON_GOUT_PURINE_TERMS,
    COMMON_KIDNEY_HIGH_PROTEIN_TERMS,
    COMMON_PREGNANCY_FOOD_SAFETY_TERMS,
    COMMON_SODIUM_HEAVY_TERMS,
    COMMON_SUGAR_HEAVY_TERMS,
)
from app.schemas.home import (
    DietRecommendationItem,
    DietRecommendationSlots,
    HomeRecommendationResponse,
    HomeRecommendationScope,
    WorkoutRecommendationItem,
    WorkoutRecommendationSlots,
)

KST = ZoneInfo("Asia/Seoul")
WORKOUT_SLOTS = ("upper_body", "lower_body", "cardio", "stretching")
DIET_SLOTS = ("breakfast", "lunch", "dinner")
PROMPT_PATH = "home/recommendations.md"

_WORKOUT_FALLBACKS = {
    "upper_body": (
        {
            "exercise_name": "푸시업",
            "summary": "상체를 안정적으로 자극하는 기본 운동입니다.",
            "sets": 3,
            "calories": 120,
        },
        {
            "exercise_name": "밴드 로우",
            "summary": "어깨 부담을 낮추며 등을 쓰는 상체 운동입니다.",
            "sets": 3,
            "calories": 110,
        },
        {
            "exercise_name": "월 푸시업",
            "summary": "강도를 낮춘 가벼운 상체 운동입니다.",
            "sets": 3,
            "calories": 90,
        },
    ),
    "lower_body": (
        {
            "exercise_name": "스쿼트",
            "summary": "하체 전반을 고르게 쓰는 기본 운동입니다.",
            "sets": 3,
            "calories": 140,
        },
        {
            "exercise_name": "글루트 브릿지",
            "summary": "무릎 부담을 줄이며 둔근을 쓰는 하체 운동입니다.",
            "sets": 3,
            "calories": 100,
        },
        {
            "exercise_name": "의자 스쿼트",
            "summary": "안정적으로 반복하기 쉬운 하체 운동입니다.",
            "sets": 3,
            "calories": 110,
        },
    ),
    "cardio": (
        {
            "exercise_name": "빠른 걷기",
            "summary": "부담이 적은 저강도 유산소 운동입니다.",
            "duration_minutes": 25,
            "calories": 160,
        },
        {
            "exercise_name": "실내 자전거",
            "summary": "관절 부담을 줄인 유산소 운동입니다.",
            "duration_minutes": 20,
            "calories": 170,
        },
        {
            "exercise_name": "제자리 걷기",
            "summary": "집에서도 바로 할 수 있는 유산소 운동입니다.",
            "duration_minutes": 20,
            "calories": 120,
        },
    ),
    "stretching": (
        {
            "exercise_name": "전신 스트레칭",
            "summary": "온몸을 가볍게 풀어주는 회복 루틴입니다.",
            "sets": 2,
            "calories": 50,
        },
        {
            "exercise_name": "고양이-소 스트레칭",
            "summary": "등과 허리의 긴장을 부드럽게 완화합니다.",
            "sets": 2,
            "calories": 40,
        },
        {
            "exercise_name": "햄스트링 스트레칭",
            "summary": "하체 뻣뻣함을 줄이는 기본 스트레칭입니다.",
            "sets": 2,
            "calories": 35,
        },
    ),
}

_DIET_FALLBACKS = {
    "breakfast": (
        {
            "food_name": "바나나 현미죽",
            "summary": "아침에 부담이 적은 탄수화물 중심 식사입니다.",
            "calories": 320,
            "diet_types": {"any", "vegetarian", "vegan"},
            "allergens": set(),
        },
        {
            "food_name": "오트밀 과일 볼",
            "summary": "포만감을 주는 간단한 아침 식사입니다.",
            "calories": 340,
            "diet_types": {"any", "vegetarian", "vegan"},
            "allergens": {"gluten"},
        },
        {
            "food_name": "그릭요거트 베리 볼",
            "summary": "단백질과 과일을 함께 챙기기 좋은 아침 식사입니다.",
            "calories": 360,
            "diet_types": {"any", "vegetarian"},
            "allergens": {"dairy"},
        },
    ),
    "lunch": (
        {
            "food_name": "현미 채소 비빔볼",
            "summary": "채소와 곡물을 균형 있게 담은 점심 식사입니다.",
            "calories": 480,
            "diet_types": {"any", "vegetarian", "vegan"},
            "allergens": set(),
        },
        {
            "food_name": "두부 샐러드 볼",
            "summary": "가볍지만 포만감이 있는 점심 식사입니다.",
            "calories": 430,
            "diet_types": {"any", "vegetarian", "vegan"},
            "allergens": {"soy"},
        },
        {
            "food_name": "닭가슴살 현미볼",
            "summary": "단백질을 보강한 점심 식사입니다.",
            "calories": 520,
            "diet_types": {"any"},
            "allergens": set(),
        },
    ),
    "dinner": (
        {
            "food_name": "버섯 채소 덮밥",
            "summary": "저녁에 부담을 줄인 따뜻한 식사입니다.",
            "calories": 500,
            "diet_types": {"any", "vegetarian", "vegan"},
            "allergens": set(),
        },
        {
            "food_name": "연어 채소 플레이트",
            "summary": "단백질과 채소를 함께 챙기는 저녁 식사입니다.",
            "calories": 540,
            "diet_types": {"any"},
            "allergens": {"fish"},
        },
        {
            "food_name": "렌틸 현미 플레이트",
            "summary": "포만감이 긴 식물성 저녁 식사입니다.",
            "calories": 510,
            "diet_types": {"any", "vegetarian", "vegan"},
            "allergens": set(),
        },
    ),
}


def kst_today_iso() -> str:
    return datetime.now(KST).date().isoformat()


def build_home_recommendation_prompt_input(
    *,
    date: str,
    scope: HomeRecommendationScope,
    user_profile: dict,
    today_plan: list[dict],
    recent_recommendations: dict | None = None,
) -> str:
    today_exercise_names: list[str] = []
    today_meal_names: list[str] = []
    workout_by_slot = {slot: [] for slot in WORKOUT_SLOTS}
    diet_by_slot = {slot: [] for slot in DIET_SLOTS}

    for item in today_plan or []:
        item_type = str(item.get("type") or "").strip().lower()
        slot_name = str(item.get("name") or "").strip().lower()
        detail = str(item.get("detail") or "").strip()
        if not detail:
            continue

        if item_type == "exercise":
            today_exercise_names.append(detail)
            if slot_name in workout_by_slot:
                workout_by_slot[slot_name].append(detail)
            continue

        if item_type == "meal":
            today_meal_names.append(detail)
            if slot_name in diet_by_slot:
                diet_by_slot[slot_name].append(detail)

    profile = {
        "goal": user_profile.get("goal"),
        "activity_level": user_profile.get("activity_level"),
        "diet_type": user_profile.get("diet_type"),
        "allergies": user_profile.get("allergies") or [],
        "injury_history": user_profile.get("injury_history") or [],
        "age": user_profile.get("age"),
        "gender": user_profile.get("gender"),
        "weight": user_profile.get("weight"),
        "height": user_profile.get("height"),
        "exercise_level": user_profile.get("exercise_level") or user_profile.get("fitness_level"),
        "exercise_frequency": user_profile.get("exercise_frequency") or user_profile.get("workout_frequency"),
        "available_time_minutes": user_profile.get("available_time_minutes"),
        "social_orientation": user_profile.get("social_orientation")
        or user_profile.get("personality_axis")
        or user_profile.get("personality_type")
        or user_profile.get("exercise_style"),
        "primary_goal": user_profile.get("primary_goal"),
        "diet_goal": user_profile.get("diet_goal"),
    }

    recent = recent_recommendations or {}
    recent_workout = recent.get("workout") or {}
    recent_diet = recent.get("diet") or {}

    return "\n\n".join(
        [
            f"[DATE]\n{date}",
            f"[SCOPE]\n{scope}",
            f"[USER_PROFILE]\n{json.dumps(profile, ensure_ascii=False)}",
            f"[TODAY_EXERCISE_EXCLUDE]\n{json.dumps(today_exercise_names, ensure_ascii=False)}",
            f"[TODAY_DIET_EXCLUDE]\n{json.dumps(today_meal_names, ensure_ascii=False)}",
            f"[TODAY_EXERCISE_BY_SLOT]\n{json.dumps(workout_by_slot, ensure_ascii=False)}",
            f"[TODAY_DIET_BY_SLOT]\n{json.dumps(diet_by_slot, ensure_ascii=False)}",
            f"[RECENT_WORKOUT_RECOMMENDATIONS]\n{json.dumps(recent_workout, ensure_ascii=False)}",
            f"[RECENT_DIET_RECOMMENDATIONS]\n{json.dumps(recent_diet, ensure_ascii=False)}",
        ]
    )


def normalize_home_recommendations(
    response: HomeRecommendationResponse,
    *,
    scope: HomeRecommendationScope,
    date: str,
    user_profile: dict | None = None,
    today_plan: list[dict] | None = None,
    recent_recommendations: dict | None = None,
) -> HomeRecommendationResponse:
    workout = WorkoutRecommendationSlots(
        upper_body=_normalize_workout_item("upper_body", response.workout.upper_body),
        lower_body=_normalize_workout_item("lower_body", response.workout.lower_body),
        cardio=_normalize_workout_item("cardio", response.workout.cardio),
        stretching=_normalize_workout_item("stretching", response.workout.stretching),
    )
    diet = DietRecommendationSlots(
        breakfast=_normalize_diet_item(response.diet.breakfast),
        lunch=_normalize_diet_item(response.diet.lunch),
        dinner=_normalize_diet_item(response.diet.dinner),
    )

    if scope == "workout":
        diet = DietRecommendationSlots()
    elif scope == "diet":
        workout = WorkoutRecommendationSlots()
    else:
        workout = _fill_missing_workout_slots(
            workout,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_recommendations=recent_recommendations,
        )
        diet = _fill_missing_diet_slots(
            diet,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_recommendations=recent_recommendations,
        )

    if scope == "workout":
        workout = _fill_missing_workout_slots(
            workout,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_recommendations=recent_recommendations,
        )
    elif scope == "diet":
        diet = _fill_missing_diet_slots(
            diet,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_recommendations=recent_recommendations,
        )

    if scope in {"all", "workout"}:
        workout = _guard_workout_slots_for_profile(
            workout,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_recommendations=recent_recommendations,
        )
    if scope in {"all", "diet"}:
        diet = _guard_diet_slots_for_profile(
            diet,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_recommendations=recent_recommendations,
        )

    return HomeRecommendationResponse(
        date=date,
        scope=scope,
        workout=workout,
        diet=diet,
    )


def empty_home_recommendations(
    *,
    date: str,
    scope: HomeRecommendationScope,
    user_profile: dict | None = None,
    today_plan: list[dict] | None = None,
    recent_recommendations: dict | None = None,
) -> HomeRecommendationResponse:
    response = HomeRecommendationResponse(date=date, scope=scope)
    if scope == "workout":
        response.diet = DietRecommendationSlots()
    elif scope == "diet":
        response.workout = WorkoutRecommendationSlots()
    return normalize_home_recommendations(
        response,
        scope=scope,
        date=date,
        user_profile=user_profile,
        today_plan=today_plan,
        recent_recommendations=recent_recommendations,
    )


def validate_home_recommendation_profile_fit(
    response: HomeRecommendationResponse,
    *,
    user_profile: dict | None,
) -> list[dict]:
    issues: list[dict] = []
    injury_tokens = _profile_tokens(user_profile, "injury_history") | _profile_tokens(user_profile, "pain_points")

    for slot, item in response.workout.model_dump().items():
        if not item:
            continue
        text = f"{item.get('exercise_name') or ''} {item.get('summary') or ''}"
        if not _is_workout_candidate_safe(slot, text, injury_tokens):
            issues.append(
                {
                    "severity": "critical",
                    "code": "home_workout_profile_conflict",
                    "slot": slot,
                    "item": item.get("exercise_name"),
                }
            )

    for slot, item in response.diet.model_dump().items():
        if not item:
            continue
        diet_item = DietRecommendationItem(**item)
        if _diet_item_conflicts_profile(diet_item, user_profile):
            issues.append(
                {
                    "severity": "critical",
                    "code": "home_diet_profile_conflict",
                    "slot": slot,
                    "item": diet_item.food_name,
                }
            )

    return issues


def _fill_missing_workout_slots(
    slots: WorkoutRecommendationSlots,
    *,
    user_profile: dict | None,
    today_plan: list[dict] | None,
    recent_recommendations: dict | None,
) -> WorkoutRecommendationSlots:
    recent_workout = (recent_recommendations or {}).get("workout") or {}
    return WorkoutRecommendationSlots(
        upper_body=slots.upper_body
        or _build_workout_fallback(
            "upper_body",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_workout.get("upper_body") or [],
        ),
        lower_body=slots.lower_body
        or _build_workout_fallback(
            "lower_body",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_workout.get("lower_body") or [],
        ),
        cardio=slots.cardio
        or _build_workout_fallback(
            "cardio",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_workout.get("cardio") or [],
        ),
        stretching=slots.stretching
        or _build_workout_fallback(
            "stretching",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_workout.get("stretching") or [],
        ),
    )


def _fill_missing_diet_slots(
    slots: DietRecommendationSlots,
    *,
    user_profile: dict | None,
    today_plan: list[dict] | None,
    recent_recommendations: dict | None,
) -> DietRecommendationSlots:
    recent_diet = (recent_recommendations or {}).get("diet") or {}
    return DietRecommendationSlots(
        breakfast=slots.breakfast
        or _build_diet_fallback(
            "breakfast",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_diet.get("breakfast") or [],
        ),
        lunch=slots.lunch
        or _build_diet_fallback(
            "lunch",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_diet.get("lunch") or [],
        ),
        dinner=slots.dinner
        or _build_diet_fallback(
            "dinner",
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_diet.get("dinner") or [],
        ),
    )


def _guard_workout_slots_for_profile(
    slots: WorkoutRecommendationSlots,
    *,
    user_profile: dict | None,
    today_plan: list[dict] | None,
    recent_recommendations: dict | None,
) -> WorkoutRecommendationSlots:
    recent_workout = (recent_recommendations or {}).get("workout") or {}
    injury_tokens = _profile_tokens(user_profile, "injury_history") | _profile_tokens(user_profile, "pain_points")

    def keep_or_replace(slot: str, item: WorkoutRecommendationItem | None) -> WorkoutRecommendationItem | None:
        if item is None:
            return None
        text = f"{item.exercise_name} {item.summary}"
        if _is_workout_candidate_safe(slot, text, injury_tokens):
            return item
        return _build_workout_fallback(
            slot,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_workout.get(slot) or [],
        )

    return WorkoutRecommendationSlots(
        upper_body=keep_or_replace("upper_body", slots.upper_body),
        lower_body=keep_or_replace("lower_body", slots.lower_body),
        cardio=keep_or_replace("cardio", slots.cardio),
        stretching=keep_or_replace("stretching", slots.stretching),
    )


def _guard_diet_slots_for_profile(
    slots: DietRecommendationSlots,
    *,
    user_profile: dict | None,
    today_plan: list[dict] | None,
    recent_recommendations: dict | None,
) -> DietRecommendationSlots:
    recent_diet = (recent_recommendations or {}).get("diet") or {}

    def keep_or_replace(slot: str, item: DietRecommendationItem | None) -> DietRecommendationItem | None:
        if item is None:
            return None
        if not _diet_item_conflicts_profile(item, user_profile):
            return item
        return _build_diet_fallback(
            slot,
            user_profile=user_profile,
            today_plan=today_plan,
            recent_names=recent_diet.get(slot) or [],
        )

    return DietRecommendationSlots(
        breakfast=keep_or_replace("breakfast", slots.breakfast),
        lunch=keep_or_replace("lunch", slots.lunch),
        dinner=keep_or_replace("dinner", slots.dinner),
    )


def _build_workout_fallback(
    slot: str,
    *,
    user_profile: dict | None,
    today_plan: list[dict] | None,
    recent_names: list[str],
) -> WorkoutRecommendationItem:
    excluded_names = _build_excluded_names(today_plan, recent_names, item_type="exercise")
    injury_tokens = _profile_tokens(user_profile, "injury_history")

    for candidate in _ordered_workout_candidates(slot, user_profile):
        exercise_name = candidate["exercise_name"]
        if _normalize_name(exercise_name) in excluded_names:
            continue
        if not _is_workout_candidate_safe(slot, exercise_name, injury_tokens):
            continue
        candidate = _personalize_workout_candidate(slot, candidate, user_profile)
        return _normalize_workout_item(
            slot,
            WorkoutRecommendationItem(**candidate),
        ) or WorkoutRecommendationItem(**candidate)

    first_candidate = _personalize_workout_candidate(slot, _ordered_workout_candidates(slot, user_profile)[0], user_profile)
    return _normalize_workout_item(
        slot,
        WorkoutRecommendationItem(**first_candidate),
    ) or WorkoutRecommendationItem(**first_candidate)


def _ordered_workout_candidates(slot: str, user_profile: dict | None) -> list[dict]:
    candidates = [dict(candidate) for candidate in _WORKOUT_FALLBACKS[slot]]
    return sorted(
        candidates,
        key=lambda candidate: _workout_candidate_score(slot, candidate, user_profile),
        reverse=True,
    )


def _workout_candidate_score(slot: str, candidate: dict, user_profile: dict | None) -> int:
    profile = user_profile or {}
    orientation = _profile_social_orientation(profile)
    goal_text = _profile_goal_text(profile)
    exercise_name = _normalize_name(str(candidate.get("exercise_name") or ""))
    summary = _normalize_name(str(candidate.get("summary") or ""))
    text = f"{exercise_name} {summary}"
    score = 0

    if orientation == "introvert":
        if any(marker in text for marker in ("집", "홈", "실내", "제자리", "월", "의자", "브릿지", "전신")):
            score += 8
        if any(marker in text for marker in ("친구", "그룹", "함께")):
            score -= 6
    elif orientation == "extrovert":
        if any(marker in text for marker in ("걷기", "수업", "챌린지", "함께", "친구")):
            score += 4

    if slot == "cardio" and _is_fat_loss_goal_text(goal_text):
        score += int(candidate.get("duration_minutes") or 0) // 5
        if any(marker in text for marker in ("걷기", "자전거", "제자리")):
            score += 4
    if slot == "stretching" and any(marker in goal_text for marker in ("health", "mobility", "건강", "가동성")):
        score += 3
    return score


def _personalize_workout_candidate(slot: str, candidate: dict, user_profile: dict | None) -> dict:
    profile = user_profile or {}
    next_candidate = dict(candidate)
    orientation = _profile_social_orientation(profile)
    goal_text = _profile_goal_text(profile)
    summary = str(next_candidate.get("summary") or "").strip()

    if slot == "cardio":
        if orientation == "introvert" and not any(
            marker in _normalize_name(str(next_candidate.get("exercise_name") or ""))
            for marker in ("집", "홈", "실내", "제자리")
        ):
            next_candidate["exercise_name"] = "집에서 제자리 빠른 걷기"
            summary = "집에서 혼자 할 수 있는 저충격 유산소 운동입니다."
        elif orientation == "extrovert" and not any(
            marker in _normalize_name(str(next_candidate.get("exercise_name") or ""))
            for marker in ("친구", "그룹", "함께")
        ):
            next_candidate["exercise_name"] = "친구와 빠른 걷기"
            summary = "친구와 함께 하거나 그룹 챌린지로 이어가기 쉬운 유산소 운동입니다."
        if _is_fat_loss_goal_text(goal_text):
            next_candidate["duration_minutes"] = max(25, int(next_candidate.get("duration_minutes") or 20))
            summary = f"{summary} 감량 목표에 맞춰 유산소 비중을 높였습니다.".strip()

    if slot in {"upper_body", "lower_body", "stretching"} and orientation == "introvert":
        if "집" not in summary and "홈" not in summary:
            summary = f"집에서 혼자 하기 쉬운 {summary}".strip()
    elif slot in {"upper_body", "lower_body", "stretching"} and orientation == "extrovert":
        if not any(marker in summary for marker in ("친구", "그룹", "함께")):
            summary = f"{summary} 친구와 함께 하거나 그룹 루틴에 넣기 좋습니다.".strip()

    next_candidate["summary"] = summary
    return next_candidate


def _build_diet_fallback(
    slot: str,
    *,
    user_profile: dict | None,
    today_plan: list[dict] | None,
    recent_names: list[str],
) -> DietRecommendationItem:
    excluded_names = _build_excluded_names(today_plan, recent_names, item_type="meal")
    allergies = _normalize_allergy_tokens(user_profile)
    diet_type = str((user_profile or {}).get("diet_type") or "").strip().lower()

    for candidate in _DIET_FALLBACKS[slot]:
        food_name = candidate["food_name"]
        if _normalize_name(food_name) in excluded_names:
            continue
        if not _is_diet_type_allowed(diet_type, candidate["diet_types"]):
            continue
        if allergies & set(candidate["allergens"]):
            continue
        item = DietRecommendationItem(
            food_name=food_name,
            summary=candidate["summary"],
            calories=candidate["calories"],
        )
        if _diet_item_conflicts_profile(item, user_profile):
            continue
        return _normalize_diet_item(
            item
        ) or item

    first_candidate = _safe_diet_fallback_candidate(slot, user_profile)
    return _normalize_diet_item(
        DietRecommendationItem(
            food_name=first_candidate["food_name"],
            summary=first_candidate["summary"],
            calories=first_candidate["calories"],
        )
    ) or DietRecommendationItem(
        food_name=first_candidate["food_name"],
        summary=first_candidate["summary"],
        calories=first_candidate["calories"],
    )


_HOME_DIET_ALLERGEN_TERMS = {
    "dairy": ("우유", "치즈", "요거트", "요구르트", "버터", "크림", "milk", "cheese", "yogurt", "butter", "cream"),
    "egg": ("계란", "달걀", "egg"),
    "nut": ("견과", "땅콩", "아몬드", "호두", "peanut", "almond", "walnut", "nut"),
    "gluten": ("밀", "빵", "파스타", "wheat", "gluten"),
    "soy": ("두부", "두유", "대두", "콩요거트", "템페", "soy", "soybean"),
    "fish": ("생선", "연어", "참치", "새우", "갑각류", "fish", "salmon", "tuna", "shrimp", "shellfish"),
}
_HOME_MEAT_TERMS = ("닭", "닭가슴살", "소고기", "돼지고기", "고기", "연어", "참치", "생선", "새우", "chicken", "beef", "pork", "fish")
_HOME_VEGAN_EXTRA_TERMS = ("계란", "달걀", "우유", "치즈", "요거트", "유제품", "egg", "milk", "cheese", "yogurt")
_HOME_SODIUM_TERMS = ("라면", "햄", "소시지", "베이컨", "젓갈", "국물", "짠", "나트륨", *COMMON_SODIUM_HEAVY_TERMS)
_HOME_SUGAR_TERMS = ("설탕", "시럽", "탄산", "주스", "케이크", "과자", "디저트", *COMMON_SUGAR_HEAVY_TERMS)
_HOME_KIDNEY_TERMS = ("고단백", "프로틴", "단백질 쉐이크", "크레아틴", "식사대용 쉐이크", "단백질바", *COMMON_KIDNEY_HIGH_PROTEIN_TERMS)
_HOME_GOUT_TERMS = ("내장", "곱창", "멸치", "정어리", "맥주", "조개", "새우", *COMMON_GOUT_PURINE_TERMS)
_HOME_PREGNANCY_TERMS = ("생선회", "회", "날달걀", "알코올", "술", "와인", "맥주", *COMMON_PREGNANCY_FOOD_SAFETY_TERMS)
_HOME_EATING_RISK_TERMS = ("900kcal", "800kcal", "단식", "굶", "하루 한 끼", "원푸드", "절식", *COMMON_EATING_DISORDER_RISK_TERMS)


def _diet_item_conflicts_profile(item: DietRecommendationItem, user_profile: dict | None) -> bool:
    profile_text = _home_profile_constraint_text(user_profile)
    item_text = _normalize_name(f"{item.food_name} {item.summary}")
    allergies = _normalize_allergy_tokens(user_profile)

    for allergy in allergies:
        terms = _HOME_DIET_ALLERGEN_TERMS.get(allergy)
        if terms and any(term.lower() in item_text for term in terms):
            return True

    if "vegetarian" in profile_text and any(term.lower() in item_text for term in _HOME_MEAT_TERMS):
        return True
    if "vegan" in profile_text and any(term.lower() in item_text for term in (*_HOME_MEAT_TERMS, *_HOME_VEGAN_EXTRA_TERMS)):
        return True
    if any(marker in profile_text for marker in ("고혈압", "혈압", "hypertension")) and any(term in item_text for term in _HOME_SODIUM_TERMS):
        return True
    if any(marker in profile_text for marker in ("당뇨", "혈당", "diabetes", "glucose")) and any(term in item_text for term in _HOME_SUGAR_TERMS):
        return True
    if any(marker in profile_text for marker in ("신장", "콩팥", "ckd", "kidney", "renal")) and any(term in item_text for term in _HOME_KIDNEY_TERMS):
        return True
    if any(marker in profile_text for marker in ("통풍", "요산", "gout", "uric acid")) and any(term in item_text for term in _HOME_GOUT_TERMS):
        return True
    if any(marker in profile_text for marker in ("임신", "임산부", "pregnancy", "pregnant")) and any(term in item_text for term in _HOME_PREGNANCY_TERMS):
        return True
    if any(marker in profile_text for marker in ("섭식", "폭식", "절식", "eating disorder")) and any(term in item_text for term in _HOME_EATING_RISK_TERMS):
        return True
    return False


def _safe_diet_fallback_candidate(slot: str, user_profile: dict | None) -> dict:
    profile_text = _home_profile_constraint_text(user_profile)
    plant_based = any(marker in profile_text for marker in ("vegetarian", "vegan", "채식", "비건"))
    high_risk = any(
        marker in profile_text
        for marker in ("신장", "콩팥", "ckd", "kidney", "renal", "통풍", "요산", "gout", "임신", "임산부", "pregnancy", "섭식", "폭식", "절식")
    )
    defaults = {
        "breakfast": {
            "food_name": "현미죽과 블루베리",
            "summary": "부담을 낮춘 간단한 아침 식사입니다.",
            "calories": 300,
        },
        "lunch": {
            "food_name": "현미밥과 구운 채소",
            "summary": "자극적인 재료를 줄인 점심 식사입니다.",
            "calories": 430,
        },
        "dinner": {
            "food_name": "고구마 채소 수프",
            "summary": "가볍게 마무리하기 좋은 저녁 식사입니다.",
            "calories": 360,
        },
    }
    if plant_based and not high_risk:
        defaults["lunch"] = {
            "food_name": "병아리콩 현미볼",
            "summary": "채식 기준으로 단백질을 보탠 점심 식사입니다.",
            "calories": 470,
        }
    return defaults.get(slot, defaults["lunch"])


def _home_profile_constraint_text(user_profile: dict | None) -> str:
    profile = user_profile or {}
    chunks: list[str] = []
    for key in (
        "diet_type",
        "dietary_restrictions",
        "dietary_preferences",
        "foods_to_avoid",
        "allergies",
        "allergy",
        "medical_history",
        "medical_conditions",
        "conditions",
        "goal",
        "diet_goal",
        "primary_goal",
        "context_notes",
    ):
        chunks.extend(str(value) for value in _as_list(profile.get(key)))
    return " ".join(chunks).lower()


def _as_list(value: object) -> list[object]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return list(value.values())
    return [value]


def _profile_social_orientation(user_profile: dict | None) -> str | None:
    profile = user_profile or {}
    for key in (
        "social_orientation",
        "personality_axis",
        "personality_type",
        "personality",
        "exercise_style",
        "introversion_extroversion",
    ):
        value = profile.get(key)
        if not value:
            continue
        text = str(value).strip().lower()
        if text in {"e", "extrovert", "extroverted", "extravert", "extraverted", "외향", "외향형"}:
            return "extrovert"
        if text in {"i", "introvert", "introverted", "내향", "내향형"}:
            return "introvert"
        if any(marker in text for marker in ("외향", "extro", "extra", "social", "group", "함께")):
            return "extrovert"
        if any(marker in text for marker in ("내향", "intro", "solo", "quiet", "혼자", "조용")):
            return "introvert"

    mbti = str(profile.get("mbti") or "").strip().lower()
    if len(mbti) == 4 and mbti[0] in {"e", "i"}:
        return "extrovert" if mbti.startswith("e") else "introvert"
    return None


def _profile_goal_text(user_profile: dict | None) -> str:
    profile = user_profile or {}
    return " ".join(
        str(value)
        for value in (
            profile.get("goal"),
            profile.get("diet_goal"),
            profile.get("diet_type"),
            profile.get("primary_goal"),
        )
        if value
    ).lower()


def _is_fat_loss_goal_text(goal_text: str) -> bool:
    return any(
        marker in goal_text
        for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량", "체중 감량")
    )


def _build_excluded_names(
    today_plan: list[dict] | None,
    recent_names: list[str],
    *,
    item_type: str,
) -> set[str]:
    excluded = {_normalize_name(name) for name in recent_names if str(name).strip()}

    for item in today_plan or []:
        if str(item.get("type") or "").strip().lower() != item_type:
            continue
        detail = str(item.get("detail") or "").strip()
        if detail:
            excluded.add(_normalize_name(detail))

    return excluded


def _normalize_name(value: str) -> str:
    return str(value or "").strip().lower()


def _profile_tokens(user_profile: dict | None, field_name: str) -> set[str]:
    raw = (user_profile or {}).get(field_name) or []
    values = raw if isinstance(raw, list) else [raw]
    tokens: set[str] = set()

    for value in values:
        normalized = _normalize_name(str(value))
        if normalized:
            tokens.add(normalized)

    return tokens


def _normalize_allergy_tokens(user_profile: dict | None) -> set[str]:
    allergies = _profile_tokens(user_profile, "allergies")
    normalized: set[str] = set()

    alias_map = {
        "우유": "dairy",
        "유제품": "dairy",
        "milk": "dairy",
        "치즈": "dairy",
        "요거트": "dairy",
        "계란": "egg",
        "달걀": "egg",
        "egg": "egg",
        "땅콩": "nut",
        "견과": "nut",
        "nut": "nut",
        "peanut": "nut",
        "대두": "soy",
        "콩": "soy",
        "soy": "soy",
        "밀": "gluten",
        "gluten": "gluten",
        "wheat": "gluten",
        "생선": "fish",
        "fish": "fish",
        "해산물": "fish",
    }

    for allergy in allergies:
        matched = False
        for alias, canonical in alias_map.items():
            if alias in allergy:
                normalized.add(canonical)
                matched = True
        if not matched:
            normalized.add(allergy)

    return normalized


def _is_diet_type_allowed(diet_type: str, allowed_types: set[str]) -> bool:
    if not diet_type or "any" in allowed_types:
        return True
    if diet_type == "vegan":
        return "vegan" in allowed_types
    if diet_type == "vegetarian":
        return "vegetarian" in allowed_types or "vegan" in allowed_types
    return "any" in allowed_types


def _is_workout_candidate_safe(slot: str, exercise_name: str, injury_tokens: set[str]) -> bool:
    if not injury_tokens:
        return True

    normalized_name = _normalize_name(exercise_name)
    joined_tokens = " ".join(sorted(injury_tokens))

    if slot == "upper_body" and any(token in joined_tokens for token in ("어깨", "shoulder", "손목", "wrist")):
        return "밴드" in normalized_name or "월" in normalized_name

    if slot == "lower_body" and any(token in joined_tokens for token in ("무릎", "knee", "발목", "ankle")):
        return "브릿지" in normalized_name or "의자" in normalized_name

    if slot == "cardio" and any(token in joined_tokens for token in ("무릎", "knee", "발목", "ankle", "허리", "back")):
        return "걷기" in normalized_name or "자전거" in normalized_name

    if slot == "stretching" and "허리" in joined_tokens:
        return "고양이" in normalized_name or "전신" in normalized_name

    return True


def _normalize_workout_item(
    slot: str,
    item: WorkoutRecommendationItem | None,
) -> WorkoutRecommendationItem | None:
    if item is None:
        return None

    exercise_name = item.exercise_name.strip()
    if not exercise_name:
        return None

    calories = max(0, int(item.calories or 0))
    summary = _compact_display_text(item.summary.strip(), limit=46)

    if slot == "cardio":
        duration_minutes = int(item.duration_minutes or 20)
        return WorkoutRecommendationItem(
            exercise_name=exercise_name,
            summary=summary,
            sets=None,
            duration_minutes=max(1, duration_minutes),
            calories=calories,
        )

    sets = int(item.sets or 3)
    return WorkoutRecommendationItem(
        exercise_name=exercise_name,
        summary=summary,
        sets=max(1, sets),
        duration_minutes=None,
        calories=calories,
    )


def _normalize_diet_item(item: DietRecommendationItem | None) -> DietRecommendationItem | None:
    if item is None:
        return None

    food_name = _compact_food_name(item.food_name.strip())
    if not food_name:
        return None

    return DietRecommendationItem(
        food_name=food_name,
        summary=_compact_display_text(item.summary.strip(), limit=46),
        calories=max(0, int(item.calories or 0)),
    )


def _compact_display_text(value: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _compact_food_name(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""

    primary = text.split("/", 1)[0].strip()
    for marker in ("알레르기", "질환", "고려", "제외", "대체", "목표", "제약"):
        marker_index = primary.find(marker)
        if marker_index > 0:
            primary = primary[:marker_index].strip(" ,·/+")

    return _compact_display_text(primary, limit=34)
