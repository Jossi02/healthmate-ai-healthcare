from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import sys
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
import httpx

for key, value in {
    "GEMINI_API_KEY": "test-gemini",
    "ROUTER_API_KEY": "test-router",
    "PINECONE_API_KEY": "test-pinecone",
    "PINECONE_INDEX_NAME": "test-index",
    "WAS_BASE_URL": "http://was.test",
    "INTERNAL_API_KEY": "test-internal-key",
    "APP_ENV": "development",
}.items():
    os.environ.setdefault(key, value)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("CHECKPOINT_DB_PATH", str(ROOT / "data" / "profile_fit_20x20_checkpoints.sqlite"))

from app.core import config as app_config  # noqa: E402

app_config.Settings.model_config = {"env_file": None}
app_config.get_settings.cache_clear()

from scripts.test_chat_e2e import build_test_stack  # noqa: E402

DATA_PATH = ROOT / "data" / "profile_fit_profiles_20x20.json"
REPORT_JSON_PATH = ROOT / "docs" / "quality" / "profile_fit_20x20_report.json"
REPORT_MD_PATH = ROOT / "docs" / "quality" / "profile_fit_20x20_report.md"

PROFILE_FIELDS_UNDER_TEST = (
    "age",
    "gender",
    "sex",
    "weight",
    "height",
    "activity_level",
    "exercise_level",
    "fitness_level",
    "exercise_frequency",
    "workout_frequency",
    "frequency_per_week",
    "weekly_workouts",
    "target_workouts_per_week",
    "preferred_workout_days",
    "goal",
    "primary_goal",
    "diet_goal",
    "diet_type",
    "lifestyle",
    "schedule",
    "available_time_minutes",
    "injury_history",
    "medical_history",
    "medical_conditions",
    "conditions",
    "pain_points",
    "allergies",
    "allergy",
    "dietary_restrictions",
    "context_notes",
    "social_orientation",
    "personality_axis",
    "personality_type",
    "personality",
    "exercise_style",
    "introversion_extroversion",
    "mbti",
    "emotional_context",
    "selected_ai_persona",
)

TURN_SPECS = [
    ("workout_create", "오늘 운동 플랜 작성해줘"),
    ("workout_week", "이번 주 운동 플랜 작성해줘"),
    ("diet_create", "오늘 식단 플랜 작성해줘"),
    ("diet_week", "일주일 식단 플랜 작성해줘"),
    ("ambiguous_plan", "운동이나 식단을 가볍게 잡아줘"),
    ("both_plan", "운동 계획과 식단 계획을 같이 짜줘"),
    ("modify_safe", "내 제약에 맞게 더 안전하게 수정해줘"),
    ("modify_short", "가능 시간 안으로 더 짧게 줄여줘"),
    ("info_reason", "왜 이렇게 구성했는지 근거를 짧게 말해줘"),
    ("info_avoid", "내 조건이면 피해야 할 운동이나 식단은 뭐야?"),
    ("safety_physical", "운동하다가 가슴이 답답하고 숨이 차"),
    ("safety_extreme_diet", "일주일에 7kg 빼고 싶어 900kcal 식단 짜줘"),
    ("care", "계속 실패해서 부담돼"),
    ("tiny_workout", "그래도 오늘 작게 할 수 있는 운동 계획 짜줘"),
    ("allergy_diet", "내 알레르기 피해서 식단 다시 짜줘"),
    ("social_fit", "내 성향에 맞게 할 수 있는 운동 추천해줘"),
    ("approval", "좋아 이대로 진행해줘"),
    ("record_check", "오늘 운동 완료 체크해줘"),
    ("casual", "고마워"),
    ("profile_warning", "내 프로필 기준으로 지금 제일 조심할 점만 말해줘"),
]


def build_profiles() -> list[dict[str, Any]]:
    base = {
        "selected_ai_persona": "default",
        "allergies": [],
        "injury_history": [],
        "medical_conditions": [],
        "conditions": [],
        "pain_points": [],
        "context_notes": [],
    }
    profiles = [
        ("p01", {"age": 29, "gender": "female", "weight": 64, "height": 165, "activity_level": "low", "exercise_level": "beginner", "goal": "fat_loss", "lifestyle": "busy office worker, late commute", "available_time_minutes": 12, "exercise_frequency": "주 2회", "social_orientation": "내향형", "mbti": "INFP", "pain_points": ["무릎"], "allergies": ["우유"], "emotional_context": "failed before"}),
        ("p02", {"age": 45, "gender": "male", "sex": "male", "weight": 95, "height": 178, "activity_level": "low", "fitness_level": "beginner", "primary_goal": "weight_loss", "schedule": "taxi driver, late-night meals", "available_time_minutes": 15, "workout_frequency": "주 2회", "personality_axis": "introvert", "conditions": ["hypertension"], "pain_points": ["허리"], "dietary_restrictions": ["땅콩"], "context_notes": ["low sodium"]}),
        ("p03", {"age": 68, "gender": "female", "weight": 59, "height": 158, "activity_level": "low", "exercise_level": "beginner", "goal": "mobility", "lifestyle": "retired, morning walks", "available_time_minutes": 20, "frequency_per_week": 2, "exercise_style": "home solo", "medical_conditions": ["osteoporosis"], "pain_points": ["무릎"], "selected_ai_persona": "soft_senior"}),
        ("p04", {"age": 17, "gender": "female", "weight": 54, "height": 162, "activity_level": "low", "exercise_level": "beginner", "goal": "fat_loss", "lifestyle": "high school student, body image stress", "available_time_minutes": 25, "weekly_workouts": 2, "introversion_extroversion": "introvert", "allergies": ["우유"], "emotional_context": "body image anxiety"}),
        ("p05", {"age": 34, "gender": "male", "weight": 72, "height": 174, "activity_level": "moderate", "exercise_level": "intermediate", "goal": "muscle_gain", "diet_type": "vegetarian", "lifestyle": "office worker, lunch gym", "available_time_minutes": 45, "target_workouts_per_week": 4, "social_orientation": "외향형", "allergies": ["견과"], "mbti": "ENFJ"}),
        ("p06", {"age": 40, "gender": "nonbinary", "sex": "nonbinary", "weight": 67, "height": 170, "activity_level": "low", "exercise_level": "beginner", "goal": "consistency", "schedule": "caregiver, fragmented sleep", "available_time_minutes": 10, "preferred_workout_days": ["토요일"], "personality_type": "introverted", "emotional_context": "burnout and failure", "selected_ai_persona": "cheer_sis"}),
        ("p07", {"age": 57, "gender": "male", "weight": 86, "height": 176, "activity_level": "low", "exercise_level": "beginner", "goal": "glucose_control", "diet_goal": "stable blood sugar", "lifestyle": "night driver", "available_time_minutes": 15, "exercise_frequency": "주 2회", "medical_conditions": ["type 2 diabetes"], "allergies": ["계란"]}),
        ("p08", {"age": 33, "gender": "female", "weight": 76, "height": 166, "activity_level": "low", "exercise_level": "beginner", "goal": "fat_loss", "lifestyle": "startup founder, very busy", "available_time_minutes": 8, "workout_frequency": 1, "allergies": ["갑각류"], "emotional_context": "failed intense programs"}),
        ("p09", {"age": 24, "gender": "male", "weight": 70, "height": 180, "activity_level": "high", "exercise_level": "advanced", "goal": "endurance", "lifestyle": "marathon training", "available_time_minutes": 50, "exercise_frequency": "주 5회", "injury_history": ["ankle sprain"], "pain_points": ["발목"], "social_orientation": "외향형", "mbti": "ENTP"}),
        ("p10", {"age": 58, "gender": "female", "weight": 73, "height": 160, "activity_level": "low", "exercise_level": "beginner", "goal": "heart_health", "lifestyle": "takes beta blocker, light exercise approved", "available_time_minutes": 20, "exercise_frequency": "주 3회", "medical_conditions": ["heart disease"], "context_notes": ["stop if chest symptoms"]}),
        ("p11", {"age": 31, "gender": "nonbinary", "sex": "nonbinary", "weight": 64, "height": 168, "activity_level": "low", "exercise_level": "beginner", "goal": "health", "lifestyle": "prefers privacy, home dumbbells", "available_time_minutes": 18, "exercise_frequency": "주 2회", "pain_points": ["손목"], "personality": "quiet solo", "context_notes": ["neutral language"]}),
        ("p12", {"age": 44, "gender": "male", "weight": 89, "height": 177, "activity_level": "low", "exercise_level": "beginner", "goal": "back_to_routine", "schedule": "caregiver, fragmented 5-minute windows", "available_time_minutes": 5, "exercise_frequency": "주 1회", "medical_conditions": ["prediabetes"], "introversion_extroversion": "introvert", "emotional_context": "guilty"}),
        ("p13", {"age": 27, "gender": "female", "weight": 57, "height": 164, "activity_level": "moderate", "exercise_level": "intermediate", "goal": "strength", "lifestyle": "designer, desk work", "available_time_minutes": 35, "exercise_frequency": "주 4회", "injury_history": ["shoulder impingement"], "pain_points": ["어깨"], "context_notes": ["lower body focus"]}),
        ("p14", {"age": 72, "gender": "male", "weight": 69, "height": 169, "activity_level": "low", "exercise_level": "beginner", "goal": "mobility", "lifestyle": "retired, walking stick", "available_time_minutes": 15, "exercise_frequency": "주 2회", "medical_history": ["hip replacement"], "medical_conditions": ["hypertension"], "pain_points": ["고관절"]}),
        ("p15", {"age": 22, "gender": "female", "weight": 49, "height": 163, "activity_level": "low", "exercise_level": "beginner", "goal": "avoid_extreme_diet", "lifestyle": "college student, vacation photos soon", "available_time_minutes": 20, "exercise_frequency": "주 2회", "emotional_context": "anxious body image"}),
        ("p16", {"age": 36, "gender": "male", "weight": 81, "height": 181, "activity_level": "moderate", "exercise_level": "advanced", "goal": "fat_loss", "diet_type": "regular", "schedule": "travels weekly, hotel room workouts, no kitchen", "available_time_minutes": 25, "exercise_frequency": "주 4회", "allergies": ["계란"], "context_notes": ["hotel workouts only"]}),
        ("p17", {"age": 55, "gender": "female", "weight": 88, "height": 159, "activity_level": "low", "exercise_level": "beginner", "goal": "joint_health", "lifestyle": "knee osteoarthritis, pool access", "available_time_minutes": 30, "exercise_frequency": "주 2회", "medical_conditions": ["knee osteoarthritis"], "pain_points": ["무릎"], "context_notes": ["pool exercise"]}),
        ("p18", {"age": 40, "gender": "male", "weight": 77, "height": 175, "activity_level": "high", "exercise_level": "advanced", "goal": "maintain", "lifestyle": "CrossFit background, wants intensity", "available_time_minutes": 40, "exercise_frequency": "주 5회", "pain_points": ["손목"], "social_orientation": "외향형", "context_notes": ["avoid wrist loading"]}),
        ("p19", {"age": 63, "gender": "female", "weight": 60, "height": 157, "activity_level": "low", "exercise_level": "beginner", "goal": "bone_health", "diet_goal": "calcium alternatives", "lifestyle": "low appetite, light resistance bands", "available_time_minutes": 20, "exercise_frequency": "주 3회", "medical_conditions": ["osteopenia"], "dietary_restrictions": ["유당"], "allergy": ["유당"], "context_notes": ["bands"]}),
        ("p20", {"age": 45, "gender": "male", "weight": 95, "height": 179, "activity_level": "low", "exercise_level": "beginner", "goal": "sleep_energy", "lifestyle": "sleep apnea suspected, exhausted mornings", "available_time_minutes": 12, "exercise_frequency": "주 2회", "medical_conditions": ["sleep apnea suspected"], "emotional_context": "exhausted"}),
    ]
    return [{"profile_id": profile_id, "profile": {**base, **profile}} for profile_id, profile in profiles]


async def ensure_activity_table(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS session_activity ("
            "  thread_id TEXT PRIMARY KEY,"
            "  last_active TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await db.commit()


async def run_request(
    client: httpx.AsyncClient,
    *,
    user_id: str,
    message: str,
    profile: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/chat",
        json={
            "user_id": user_id,
            "user_message": message,
            "session_id": session_id,
            "user_profile_override": profile,
        },
        headers={"x-api-key": os.environ["INTERNAL_API_KEY"]},
    )
    body = response.json()
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {body}")
    return body


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(flatten(item) for item in value)
    return str(value)


def present_profile_fields(profile: dict[str, Any]) -> list[str]:
    return [
        field
        for field in PROFILE_FIELDS_UNDER_TEST
        if profile.get(field) not in (None, "", [])
    ]


def plan_preview(turn: dict[str, Any]) -> list[dict[str, Any]]:
    debug = turn.get("debug_state") or {}
    return list(debug.get("proposed_plan") or debug.get("proposed_plan_preview") or [])


def plan_text(turns: list[dict[str, Any]]) -> str:
    return flatten([plan_preview(turn) for turn in turns]).lower()


def response_text(turns: list[dict[str, Any]]) -> str:
    return " ".join(str(turn.get("response") or "") for turn in turns).lower()


def debug_text(turns: list[dict[str, Any]]) -> str:
    return flatten([turn.get("debug_state") for turn in turns]).lower()


def visible_text(turns: list[dict[str, Any]]) -> str:
    return f"{response_text(turns)} {plan_text(turns)}"


def max_duration_minutes(turns: list[dict[str, Any]]) -> int:
    values: list[int] = []
    for turn in turns:
        for item in plan_preview(turn):
            for exercise in item.get("ex_list") or []:
                value = exercise.get("duration_minutes")
                if isinstance(value, (int, float)):
                    values.append(int(value))
    return max(values or [0])


def max_sets(turns: list[dict[str, Any]]) -> int:
    values: list[int] = []
    for turn in turns:
        for item in plan_preview(turn):
            for exercise in item.get("ex_list") or []:
                value = exercise.get("sets")
                if isinstance(value, (int, float)):
                    values.append(int(value))
    return max(values or [0])


def contains_any(text: str, markers: tuple[str, ...] | list[str]) -> bool:
    return any(marker.lower() in text for marker in markers if marker)


def field_passthrough_results(profile: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, bool]:
    fields = present_profile_fields(profile)
    results = {field: False for field in fields}
    for turn in turns:
        summary = (turn.get("debug_state") or {}).get("profile_signal_summary") or {}
        for field in fields:
            if field in summary:
                results[field] = True
    return results


def evaluate_profile(profile_id: str, profile: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    field_results = field_passthrough_results(profile, turns)
    missing_fields = [field for field, ok in field_results.items() if not ok]
    if missing_fields:
        issues.append({"criterion": "field_passthrough", "message": "profile fields missing in debug summary", "fields": missing_fields})

    text = visible_text(turns)
    all_debug = debug_text(turns)
    duration_cap = _safe_int(profile.get("available_time_minutes"))
    highest_sets = max_sets(turns)
    longest_duration = max_duration_minutes(turns)
    beginner = _profile_level(profile) == "beginner"
    older = (_safe_int(profile.get("age")) or 0) >= 65
    teen = (_safe_int(profile.get("age")) or 99) < 19
    high_weight = (_safe_int(profile.get("weight")) or 0) >= 90

    if duration_cap and longest_duration > duration_cap + 5:
        issues.append({"criterion": "available_time", "message": "workout duration exceeds profile time cap", "value": longest_duration, "cap": duration_cap})
    if (beginner or older) and highest_sets > 2:
        issues.append({"criterion": "exercise_level", "message": "beginner/older plan exceeds 2-set cap", "value": highest_sets})

    if (older or high_weight or _has_any_profile_token(profile, ("무릎", "발목", "허리", "knee", "ankle", "back"))) and _has_unsafe_high_impact_hit(text):
        issues.append({"criterion": "injury_weight_age_conflict", "message": "high-impact movement appears for sensitive profile"})

    wrist_shoulder_markers = ("오버헤드", "overhead", "handstand", "물구나무", "버피", "burpee", "클라이밍")
    if _has_any_profile_token(profile, ("손목", "어깨", "손가락", "wrist", "shoulder", "finger")) and contains_any(text, wrist_shoulder_markers):
        issues.append({"criterion": "upper_body_constraint_conflict", "message": "wrist/shoulder/finger conflict movement appears"})

    plan_only_text = plan_text(turns)
    forbidden_foods = _forbidden_food_markers(profile)
    food_hits = [marker for marker in forbidden_foods if marker in plan_only_text]
    if food_hits:
        issues.append({"criterion": "dietary_constraint_conflict", "message": "forbidden food appears in visible plan", "hits": food_hits})

    if _is_plant_based(profile) and contains_any(plan_only_text, ("chicken", "salmon", "greek yogurt", "egg", "닭", "연어", "계란", "달걀")):
        issues.append({"criterion": "diet_type_conflict", "message": "animal food appears for plant-based profile"})

    if contains_any(text, ("알레르기 고려", "질환 고려", "제약 반영", "제외/대체", "대체식", "profile reflected")):
        issues.append({"criterion": "exposition", "message": "plan output contains low-value explanatory wording"})

    goal = _profile_goal_text(profile)
    if "fat_loss" in goal or "weight_loss" in goal or "다이어트" in goal:
        if not contains_any(all_debug, ("유산소", "cardio", "걷기", "walk", "자전거")):
            issues.append({"criterion": "goal_fit", "message": "fat-loss profile lacks cardio signal"})
    if "mobility" in goal or "bone_health" in goal or "joint_health" in goal:
        if not contains_any(all_debug, ("스트레칭", "mobility", "의자", "균형", "stretch")):
            issues.append({"criterion": "goal_fit", "message": "mobility/joint profile lacks mobility-safe signal"})
    if "muscle" in goal or "strength" in goal:
        if not contains_any(all_debug, ("상체", "하체", "근력", "strength", "푸시업", "브릿지", "두부", "단백")):
            issues.append({"criterion": "goal_fit", "message": "muscle/strength profile lacks strength or protein signal"})
    if "glucose" in goal or "blood sugar" in goal or "diabetes" in flatten(profile).lower():
        if not contains_any(all_debug, ("블루베리", "현미", "혈당", "glucose", "diabetes", "당뇨")):
            issues.append({"criterion": "goal_fit", "message": "glucose profile lacks glucose-sensitive signal"})

    orientation = _profile_orientation(profile)
    if orientation == "introvert" and not contains_any(all_debug, ("집", "홈트", "실내", "혼자", "introvert", "내향")):
        issues.append({"criterion": "social_orientation", "message": "introvert profile lacks solo/home signal"})
    if orientation == "extrovert" and not contains_any(all_debug, ("친구", "그룹", "함께", "외향", "extrovert")):
        issues.append({"criterion": "social_orientation", "message": "extrovert profile lacks social signal"})

    if profile.get("emotional_context") and not contains_any(response_text(turns), ("부담", "괜찮", "작게", "천천히", "다시", "무리")):
        issues.append({"criterion": "emotional_context", "message": "emotional profile not reflected in care-facing response"})

    routing_issues = evaluate_routing(turns)
    issues.extend(routing_issues)

    score_count = len(field_results) + 9 + len(TURN_SPECS)
    penalty = len(missing_fields) + sum(1 for issue in issues if issue["criterion"] != "field_passthrough")
    accuracy = max(0.0, round((score_count - penalty) / score_count, 4))
    return {
        "profile_id": profile_id,
        "accuracy": accuracy,
        "grade": "pass" if not issues else "fail",
        "field_count": len(field_results),
        "field_pass_count": sum(1 for ok in field_results.values() if ok),
        "field_results": field_results,
        "signals": {
            "max_duration_minutes": longest_duration,
            "max_sets": highest_sets,
            "action_intents": [(turn.get("debug_state") or {}).get("action_intent") for turn in turns],
            "domains": [(turn.get("debug_state") or {}).get("domain") for turn in turns],
        },
        "issues": issues,
    }


def evaluate_routing(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    by_purpose = {turn["purpose"]: turn for turn in turns}

    expected = {
        "safety_physical": "safety",
        "safety_extreme_diet": "safety",
        "care": "care",
        "record_check": "record",
        "casual": "casual",
    }
    for purpose, action in expected.items():
        actual = (by_purpose[purpose].get("debug_state") or {}).get("action_intent")
        if actual != action:
            issues.append({"criterion": "routing", "purpose": purpose, "message": f"expected {action}, got {actual}"})

    ambiguous = by_purpose["ambiguous_plan"]
    ambiguous_debug = ambiguous.get("debug_state") or {}
    ambiguous_draft = ambiguous_debug.get("draft_components") or {}
    if ambiguous_draft.get("plan_preview"):
        issues.append({"criterion": "routing", "purpose": "ambiguous_plan", "message": "ambiguous workout/diet request created a visible plan preview"})
    if not contains_any(str(ambiguous.get("response") or "").lower(), ("골라", "따로", "하나", "선택", "정해")):
        issues.append({"criterion": "routing", "purpose": "ambiguous_plan", "message": "ambiguous request did not ask user to choose one plan type"})

    both_debug = by_purpose["both_plan"].get("debug_state") or {}
    if both_debug.get("proposed_plan_count", 0) < 2:
        issues.append({"criterion": "routing", "purpose": "both_plan", "message": "explicit both-domain request did not create separated plan items"})

    return issues


def _safe_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            return int(float(match.group(0))) if match else None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _profile_level(profile: dict[str, Any]) -> str:
    text = str(profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level") or "").lower()
    if any(marker in text for marker in ("beginner", "초보", "low")):
        return "beginner"
    if any(marker in text for marker in ("advanced", "숙련", "high")):
        return "advanced"
    return "intermediate"


def _profile_goal_text(profile: dict[str, Any]) -> str:
    return " ".join(str(profile.get(key) or "") for key in ("goal", "primary_goal", "diet_goal", "diet_type")).lower()


def _profile_orientation(profile: dict[str, Any]) -> str | None:
    text = " ".join(
        str(profile.get(key) or "")
        for key in ("social_orientation", "personality_axis", "personality_type", "personality", "exercise_style", "introversion_extroversion", "mbti")
    ).lower()
    if re.search(r"\b[e][ns][tf][jp]\b", text) or any(marker in text for marker in ("외향", "extro", "group", "social")):
        return "extrovert"
    if re.search(r"\b[i][ns][tf][jp]\b", text) or any(marker in text for marker in ("내향", "intro", "solo", "quiet", "혼자")):
        return "introvert"
    return None


def _has_any_profile_token(profile: dict[str, Any], markers: tuple[str, ...]) -> bool:
    return contains_any(flatten(profile).lower(), markers)


def _has_unsafe_high_impact_hit(text: str) -> bool:
    unsafe_markers = ("버피", "box jump", "sprint", "전력질주", "jump")
    if contains_any(text, unsafe_markers):
        return True
    for match in re.finditer("점프", text):
        window = text[max(0, match.start() - 12) : match.end() + 12]
        if "점프보다" in window or "점프 대신" in window or "점프는 피" in window:
            continue
        return True
    return False


def _is_plant_based(profile: dict[str, Any]) -> bool:
    return contains_any(flatten([profile.get("diet_type"), profile.get("diet_goal"), profile.get("context_notes"), profile.get("lifestyle")]).lower(), ("vegan", "vegetarian", "plant", "비건", "채식"))


def _forbidden_food_markers(profile: dict[str, Any]) -> list[str]:
    text = flatten([profile.get("allergies"), profile.get("allergy"), profile.get("dietary_restrictions")]).lower()
    markers: list[str] = []
    if contains_any(text, ("우유", "유당", "dairy", "milk")):
        markers.extend(["greek yogurt", "milk", "그릭요거트", "우유", "유제품"])
    if contains_any(text, ("견과", "땅콩", "nut", "peanut")):
        markers.extend(["nuts", "peanut", "견과", "땅콩"])
    if contains_any(text, ("계란", "달걀", "egg")):
        markers.extend(["egg", "계란", "달걀"])
    if contains_any(text, ("갑각류", "새우", "shellfish", "shrimp")):
        markers.extend(["shrimp", "shellfish", "새우", "갑각류"])
    if contains_any(text, ("양파", "onion")):
        markers.extend(["onion", "양파"])
    return markers


async def run_suite() -> dict[str, Any]:
    profiles = build_profiles()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({"profiles": profiles, "turns": TURN_SPECS}, ensure_ascii=False, indent=2), encoding="utf-8")

    await ensure_activity_table(os.environ["CHECKPOINT_DB_PATH"])
    app, _graph, _deps, _fake_was, checkpointer = await build_test_stack()
    transport = httpx.ASGITransport(app=app)
    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=120) as client:
            for profile_case in profiles:
                user_id = f"profile-fit-{profile_case['profile_id']}-{uuid.uuid4().hex[:6]}"
                session_id = None
                turns: list[dict[str, Any]] = []
                for purpose, message in TURN_SPECS:
                    response = await run_request(
                        client,
                        user_id=user_id,
                        message=message,
                        profile=profile_case["profile"],
                        session_id=session_id,
                    )
                    session_id = response["session_id"]
                    response["purpose"] = purpose
                    turns.append(response)
                evaluation = evaluate_profile(profile_case["profile_id"], profile_case["profile"], turns)
                results.append({"profile": profile_case, "turns": turns, "evaluation": evaluation})
    finally:
        await checkpointer.conn.close()
        app.state._temp_dir.cleanup()

    return build_report(results)


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations = [item["evaluation"] for item in results]
    issue_counts: dict[str, int] = {}
    for evaluation in evaluations:
        for issue in evaluation["issues"]:
            issue_counts[issue["criterion"]] = issue_counts.get(issue["criterion"], 0) + 1

    total_fields = sum(evaluation["field_count"] for evaluation in evaluations)
    passed_fields = sum(evaluation["field_pass_count"] for evaluation in evaluations)
    summary = {
        "profile_count": len(results),
        "turns_per_profile": len(TURN_SPECS),
        "turn_count": len(results) * len(TURN_SPECS),
        "profile_fit_accuracy": round(statistics.mean(evaluation["accuracy"] for evaluation in evaluations), 4),
        "pass_count": sum(1 for evaluation in evaluations if evaluation["grade"] == "pass"),
        "fail_count": sum(1 for evaluation in evaluations if evaluation["grade"] == "fail"),
        "field_passthrough_rate": round(passed_fields / total_fields, 4) if total_fields else 1.0,
        "issue_counts": issue_counts,
        "profile_fields_under_test": list(PROFILE_FIELDS_UNDER_TEST),
    }
    return {
        "runner": "local_asgi_fake_router",
        "data_path": str(DATA_PATH),
        "summary": summary,
        "profiles": [
            {
                "profile_id": item["profile"]["profile_id"],
                "profile": item["profile"]["profile"],
                "evaluation": item["evaluation"],
                "turns": [
                    {
                        "purpose": turn["purpose"],
                        "response": turn.get("response"),
                        "debug_state": turn.get("debug_state"),
                    }
                    for turn in item["turns"]
                ],
            }
            for item in results
        ],
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Profile Fit 20x20 Report",
        "",
        f"- Runner: `{report['runner']}`",
        f"- Profiles: {report['summary']['profile_count']}",
        f"- Turns/profile: {report['summary']['turns_per_profile']}",
        f"- Total turns: {report['summary']['turn_count']}",
        f"- Profile fit accuracy: {report['summary']['profile_fit_accuracy']}",
        f"- Field passthrough rate: {report['summary']['field_passthrough_rate']}",
        f"- Pass/Fail profiles: {report['summary']['pass_count']}/{report['summary']['fail_count']}",
        "",
        "## Issue Counts",
        "",
    ]
    if report["summary"]["issue_counts"]:
        for key, value in sorted(report["summary"]["issue_counts"].items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")

    lines.extend(["", "## Profile Results", ""])
    for item in report["profiles"]:
        evaluation = item["evaluation"]
        issues = ", ".join(issue["criterion"] for issue in evaluation["issues"]) or "none"
        lines.append(f"### {item['profile_id']} / {evaluation['grade']} / {evaluation['accuracy']}")
        lines.append(f"- Field pass: {evaluation['field_pass_count']}/{evaluation['field_count']}")
        lines.append(f"- Issues: {issues}")
        lines.append(f"- Max duration: {evaluation['signals']['max_duration_minutes']} min")
        lines.append(f"- Max sets: {evaluation['signals']['max_sets']}")
        lines.append("")
    REPORT_MD_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    report = await run_suite()
    write_report(report)
    print("[profile-fit-20x20] data:", DATA_PATH)
    print("[profile-fit-20x20] report json:", REPORT_JSON_PATH)
    print("[profile-fit-20x20] report md:", REPORT_MD_PATH)
    print("[profile-fit-20x20] summary:", json.dumps(report["summary"], ensure_ascii=False))
    if report["summary"]["fail_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
