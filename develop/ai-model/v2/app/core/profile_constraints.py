"""Profile constraint compilation shared by retrieval and validation."""
from __future__ import annotations

import json
import re
from typing import Any

_PROFILE_SIGNAL_FIELDS = (
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
)


def build_profile_constraint_set(
    profile: dict[str, Any] | None,
    query: str = "",
    *,
    domain: str = "general",
) -> dict[str, Any]:
    profile = dict(profile or {})
    query_text = str(query or "")
    profile_text = _profile_constraint_text(profile)
    profile_negative_constraints = _negative_constraints(profile_text.lower())
    query_negative_constraints = _negative_constraints(query_text.lower())
    negative_constraints = list(dict.fromkeys([*profile_negative_constraints, *query_negative_constraints]))
    profile_only_constraints = _constraint_codes_for_text(profile_text)
    bmi = profile_bmi_value(profile)
    if bmi is not None and bmi >= 25:
        profile_only_constraints.append("obesity")
    available_time = _safe_int(profile.get("available_time_minutes") or profile.get("available_time"))
    if available_time is not None and available_time <= 15:
        profile_only_constraints.append("low_time")
    profile_only_constraints = [
        constraint
        for constraint in dict.fromkeys(profile_only_constraints)
        if constraint not in profile_negative_constraints
    ]
    query_only_constraints = [
        constraint
        for constraint in _constraint_codes_for_text(query_text)
        if constraint not in query_negative_constraints
    ]
    request_hard_constraints = _request_hard_constraints(query_text, query_only_constraints)
    constraints = [
        constraint
        for constraint in dict.fromkeys([*profile_only_constraints, *query_only_constraints])
        if constraint not in negative_constraints
    ]
    retrieval_constraints = [
        constraint
        for constraint in dict.fromkeys([*profile_only_constraints, *request_hard_constraints])
        if constraint not in negative_constraints
    ]
    retrieval_constraints = _retrieval_constraints_for_domain(domain, retrieval_constraints)

    profile_targets = _profile_targets(profile)
    goals = _goals(profile, query_text)
    critical_constraints = _critical_constraints(constraints)
    retrieval_critical_constraints = _critical_constraints(retrieval_constraints)
    hard_constraints = _hard_constraint_labels(profile_only_constraints)
    safety_risks = _safety_risks([*profile_only_constraints, *request_hard_constraints], profile)
    should_use_rag = bool(
        critical_constraints
        or safety_risks
        or _profile_has_rag_risk(profile)
        or query_needs_evidence(query_text)
        or query_mentions_specialized_topic(query_text)
    )

    return {
        "domain": domain,
        "profile_targets": profile_targets,
        "constraints": constraints,
        "profile_constraints": profile_only_constraints,
        "query_constraints": query_only_constraints,
        "request_hard_constraints": request_hard_constraints,
        "retrieval_constraints": retrieval_constraints,
        "hard_profile_constraints": profile_only_constraints,
        "critical_constraints": critical_constraints,
        "retrieval_critical_constraints": retrieval_critical_constraints,
        "negative_constraints": negative_constraints,
        "profile_negative_constraints": profile_negative_constraints,
        "query_negative_constraints": query_negative_constraints,
        "goals": goals,
        "hard_constraints": hard_constraints,
        "safety_risks": safety_risks,
        "should_use_rag": should_use_rag,
        "profile_field_coverage": _profile_field_coverage(profile),
        "summary": _summary(profile, hard_constraints, goals),
    }


def _constraint_codes_for_text(text: str) -> list[str]:
    positive_text = _strip_negated_constraint_mentions(str(text or "").lower())
    return [
        constraint
        for constraint, keywords in _CONSTRAINT_MARKERS.items()
        if any(keyword in positive_text for keyword in keywords)
    ]


def _request_hard_constraints(query: str, query_constraints: list[str]) -> list[str]:
    normalized = str(query or "").lower()
    hard: list[str] = []
    pain_markers = ("아파", "통증", "부상", "불편", "재활", "다쳤", "염좌", "보호", "조심", "pain", "injury")
    diet_exclusion_markers = ("알레르기", "불내증", "못 먹", "못먹", "빼고", "제외", "없는", "없이", "피해", "avoid", "free")
    diet_preference_markers = ("채식", "비건", "vegetarian", "vegan")
    condition_markers = ("있어", "있음", "진단", "복용", "관리 중", "관리중", "고려", "맞춰", "위한", "환자", "disease", "diagnosed")

    pain_constraints = {"knee_pain", "back_pain", "shoulder_pain", "wrist_pain", "ankle_pain", "arthritis"}
    diet_constraints = {
        "food_allergy",
        "dairy_allergy",
        "egg_allergy",
        "nut_allergy",
        "shellfish_allergy",
        "wheat_allergy",
        "soy_allergy",
        "vegetarian",
        "vegan",
    }
    medical_constraints = {"hypertension", "diabetes", "cardiovascular_disease", "asthma"}

    if any(marker in normalized for marker in pain_markers):
        hard.extend(constraint for constraint in query_constraints if constraint in pain_constraints)
    if any(marker in normalized for marker in diet_exclusion_markers):
        hard.extend(constraint for constraint in query_constraints if constraint in diet_constraints)
    if any(marker in normalized for marker in diet_preference_markers):
        hard.extend(constraint for constraint in query_constraints if constraint in {"vegetarian", "vegan"})
    if any(marker in normalized for marker in condition_markers):
        hard.extend(constraint for constraint in query_constraints if constraint in medical_constraints)
    if "extreme_diet_risk" in query_constraints:
        hard.append("extreme_diet_risk")
    return list(dict.fromkeys(hard))


def query_needs_evidence(query: str) -> bool:
    normalized = str(query or "").lower()
    return any(
        keyword in normalized
        for keyword in (
            "왜",
            "근거",
            "논문",
            "연구",
            "가이드라인",
            "권고",
            "출처",
            "evidence",
            "source",
            "guideline",
        )
    )


def query_mentions_specialized_topic(query: str) -> bool:
    normalized = str(query or "").lower()
    return any(keyword in normalized for keyword in _SPECIALIZED_TOPIC_KEYWORDS)


def query_needs_user_memory(query: str) -> bool:
    normalized = str(query or "").lower()
    return any(
        keyword in normalized
        for keyword in (
            "전에",
            "지난번",
            "저번",
            "기억",
            "내가 말한",
            "싫어",
            "좋아한다고",
            "실패했던",
            "했던 방식",
        )
    )


def profile_weight_value(profile: dict[str, Any]) -> int | None:
    for key in ("weight", "body_weight", "current_weight"):
        value = profile.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return int(value)
        match = re.search(r"(\d+(?:\.\d+)?)", str(value))
        if match:
            return int(float(match.group(1)))
    return None


def profile_bmi_value(profile: dict[str, Any]) -> float | None:
    explicit = profile.get("bmi")
    try:
        if explicit not in (None, ""):
            return float(explicit)
    except (TypeError, ValueError):
        pass

    weight = profile_weight_value(profile)
    height = _height_cm_value(profile)
    if weight is None or height is None or height <= 0:
        return None
    meters = height / 100
    return round(weight / (meters * meters), 1)


def as_text_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [text for item in value if (text := _meaningful_profile_text(item))]
    if isinstance(value, dict):
        return [text for item in value.values() if (text := _meaningful_profile_text(item))]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [text for item in parsed if (text := _meaningful_profile_text(item))]
            except json.JSONDecodeError:
                pass
    text = _meaningful_profile_text(value)
    return [text] if text else []


def _meaningful_profile_text(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    normalized = re.sub(r"\s+", "", text).lower()
    if normalized in {"[]", "{}", "none", "no", "n/a", "na", "null", "없음", "해당없음", "해당사항없음", "없어요", "무"}:
        return ""
    return text


def _profile_constraint_text(profile: dict[str, Any]) -> str:
    values: list[str] = []
    for field in _PROFILE_SIGNAL_FIELDS:
        values.extend(as_text_list(profile.get(field)))
    return " ".join(values)


def _profile_targets(profile: dict[str, Any]) -> list[str]:
    targets: list[str] = ["general_adult"]
    age = _safe_int(profile.get("age"))
    if age is not None:
        if age < 19:
            targets.append("minor")
        if age >= 60:
            targets.append("older_adult")

    level = str(profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level") or "").lower()
    if any(keyword in level for keyword in ("beginner", "초보", "low", "낮", "거의 없음", "가벼운", "앉아서")):
        targets.append("beginner")
    if any(keyword in level for keyword in ("advanced", "상급", "high", "높", "격렬")):
        targets.append("advanced")

    weight = profile_weight_value(profile)
    bmi = profile_bmi_value(profile)
    if weight is not None and weight >= 90:
        targets.append("high_weight")
    if bmi is not None and bmi >= 25:
        targets.append("high_weight")

    if as_text_list(profile.get("allergies")) or as_text_list(profile.get("allergy")):
        targets.append("food_allergy")
    if _is_plant_based_profile(profile):
        targets.append("plant_based")
    return list(dict.fromkeys(targets))


def _goals(profile: dict[str, Any], query: str) -> list[str]:
    text = " ".join(
        [
            *as_text_list(profile.get("goal")),
            *as_text_list(profile.get("primary_goal")),
            *as_text_list(profile.get("diet_goal")),
            query,
        ]
    ).lower()
    goals: list[str] = []
    markers = {
        "weight_loss": ("감량", "다이어트", "체중 감소", "살 빼", "weight loss", "fat loss"),
        "muscle_gain": ("근육", "근비대", "벌크", "muscle", "hypertrophy"),
        "endurance": ("지구력", "유산소", "러닝", "endurance", "cardio"),
        "mobility": ("스트레칭", "유연성", "가동성", "mobility", "stretch"),
        "glucose_control": ("당뇨", "혈당", "glucose", "diabetes"),
        "heart_health": ("고혈압", "혈압", "심혈관", "dash", "hypertension"),
    }
    for goal, keywords in markers.items():
        if any(keyword in text for keyword in keywords):
            goals.append(goal)
    return list(dict.fromkeys(goals))


def _critical_constraints(constraints: list[str]) -> list[str]:
    non_critical = {"low_time"}
    return [constraint for constraint in constraints if constraint not in non_critical]


def _retrieval_constraints_for_domain(domain: str, constraints: list[str]) -> list[str]:
    if domain in {"general", "profile", "none", ""}:
        return []
    if domain == "workout":
        allowed = {
            "knee_pain",
            "back_pain",
            "shoulder_pain",
            "wrist_pain",
            "ankle_pain",
            "hypertension",
            "diabetes",
            "cardiovascular_disease",
            "asthma",
            "arthritis",
            "obesity",
            "low_time",
            "extreme_diet_risk",
        }
        return [constraint for constraint in constraints if constraint in allowed]
    if domain == "diet":
        allowed = {
            "hypertension",
            "diabetes",
            "cardiovascular_disease",
            "food_allergy",
            "dairy_allergy",
            "egg_allergy",
            "nut_allergy",
            "shellfish_allergy",
            "wheat_allergy",
            "soy_allergy",
            "vegetarian",
            "vegan",
            "obesity",
            "low_time",
            "extreme_diet_risk",
        }
        return [constraint for constraint in constraints if constraint in allowed]
    return constraints


def _hard_constraint_labels(constraints: list[str]) -> list[dict[str, str]]:
    labels = {
        "knee_pain": ("workout", "무릎 통증/부상"),
        "back_pain": ("workout", "허리 통증/부상"),
        "shoulder_pain": ("workout", "어깨 통증/부상"),
        "wrist_pain": ("workout", "손목 통증/부상"),
        "ankle_pain": ("workout", "발목 통증/부상"),
        "hypertension": ("diet", "고혈압/혈압 관리"),
        "diabetes": ("diet", "당뇨/혈당 관리"),
        "cardiovascular_disease": ("workout", "심혈관 질환"),
        "asthma": ("workout", "천식"),
        "arthritis": ("workout", "관절염"),
        "food_allergy": ("diet", "식품 알레르기"),
        "dairy_allergy": ("diet", "유제품/우유 제한"),
        "egg_allergy": ("diet", "계란 제한"),
        "nut_allergy": ("diet", "견과 제한"),
        "shellfish_allergy": ("diet", "갑각류 제한"),
        "wheat_allergy": ("diet", "밀/글루텐 제한"),
        "soy_allergy": ("diet", "대두 제한"),
        "vegetarian": ("diet", "채식"),
        "vegan": ("diet", "비건"),
        "obesity": ("workout", "고체중/BMI"),
        "extreme_diet_risk": ("safety", "극단적 식단 위험"),
    }
    return [
        {"code": code, "domain": labels.get(code, ("general", code))[0], "label": labels.get(code, ("general", code))[1]}
        for code in constraints
        if code in labels
    ]


def _safety_risks(constraints: list[str], profile: dict[str, Any]) -> list[str]:
    risks = []
    risk_constraints = {
        "cardiovascular_disease",
        "asthma",
        "arthritis",
        "extreme_diet_risk",
    }
    risks.extend(code for code in constraints if code in risk_constraints)
    age = _safe_int(profile.get("age"))
    if age is not None and age < 19:
        risks.append("minor")
    if age is not None and age >= 60:
        risks.append("older_adult")
    return list(dict.fromkeys(risks))


def _summary(profile: dict[str, Any], hard_constraints: list[dict[str, str]], goals: list[str]) -> dict[str, Any]:
    return {
        "age": profile.get("age"),
        "gender": profile.get("gender") or profile.get("sex"),
        "weight": profile_weight_value(profile),
        "bmi": profile_bmi_value(profile),
        "exercise_level": profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level"),
        "available_time_minutes": profile.get("available_time_minutes"),
        "lifestyle": profile.get("lifestyle") or profile.get("schedule"),
        "diet_type": profile.get("diet_type"),
        "allergies": as_text_list(profile.get("allergies") or profile.get("allergy")),
        "injury_history": as_text_list(profile.get("injury_history")),
        "pain_points": as_text_list(profile.get("pain_points")),
        "medical_conditions": as_text_list(profile.get("medical_conditions") or profile.get("conditions") or profile.get("medical_history")),
        "dietary_restrictions": as_text_list(profile.get("dietary_restrictions") or profile.get("dietary_preferences") or profile.get("foods_to_avoid")),
        "context_notes": as_text_list(profile.get("context_notes")),
        "hard_constraint_labels": [item["label"] for item in hard_constraints],
        "goals": goals,
    }


def _profile_field_coverage(profile: dict[str, Any]) -> dict[str, Any]:
    present = [
        field
        for field in _PROFILE_SIGNAL_FIELDS
        if profile.get(field) not in (None, "", [], {}, "[]")
    ]
    return {
        "present_fields": present,
        "present_count": len(present),
        "known_signal_fields": len(_PROFILE_SIGNAL_FIELDS),
    }


def _profile_has_rag_risk(profile: dict[str, Any]) -> bool:
    age = _safe_int(profile.get("age"))
    weight = profile_weight_value(profile)
    bmi = profile_bmi_value(profile)
    if age is not None and (age < 19 or age >= 60):
        return True
    if weight is not None and weight >= 90:
        return True
    if bmi is not None and bmi >= 25:
        return True
    if _is_plant_based_profile(profile):
        return True

    risk_fields = (
        "injury_history",
        "medical_history",
        "medical_conditions",
        "conditions",
        "pain_points",
        "allergies",
        "allergy",
        "dietary_restrictions",
        "dietary_preferences",
        "foods_to_avoid",
        "context_notes",
    )
    return any(as_text_list(profile.get(field)) for field in risk_fields)


def _height_cm_value(profile: dict[str, Any]) -> int | None:
    for key in ("height", "height_cm"):
        value = profile.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return int(value)
        match = re.search(r"(\d+(?:\.\d+)?)", str(value))
        if match:
            return int(float(match.group(1)))
    return None


def _is_plant_based_profile(profile: dict[str, Any]) -> bool:
    text = " ".join(
        str(profile.get(field) or "")
        for field in ("diet_type", "dietary_restrictions", "dietary_preferences", "foods_to_avoid", "goal", "context_notes")
    ).lower()
    return any(keyword in text for keyword in ("채식", "비건", "vegetarian", "vegan", "plant-based", "plant based"))


def _negative_constraints(text: str) -> list[str]:
    negatives: list[str] = []
    for constraint, pattern in _NEGATED_CONSTRAINT_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            negatives.append(constraint)
    return list(dict.fromkeys(negatives))


def _strip_negated_constraint_mentions(text: str) -> str:
    cleaned = text
    for _constraint, pattern in _NEGATED_CONSTRAINT_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _safe_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if not match:
                return None
            value = match.group(0)
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _negated_pattern(words: str, suffix: str = "") -> str:
    none_words = r"(?:해당\s*없음|해당없음|없음|없어|없어요|아님|아니야|아니에요|없고|없지만)"
    return rf"(?:{words})(?:\s*{suffix})?\s*(?:은|는|이|가|도)?\s*{none_words}"


_CONSTRAINT_MARKERS = {
    "knee_pain": ("무릎", "knee"),
    "back_pain": ("허리", "요통", "back", "sciatica"),
    "shoulder_pain": ("어깨", "shoulder"),
    "wrist_pain": ("손목", "wrist"),
    "ankle_pain": ("발목", "ankle"),
    "hypertension": ("고혈압", "혈압", "hypertension"),
    "diabetes": ("당뇨", "혈당", "diabetes", "glucose"),
    "cardiovascular_disease": ("심혈관", "심장", "협심증", "cardiovascular", "heart disease"),
    "asthma": ("천식", "asthma"),
    "arthritis": ("관절염", "arthritis"),
    "food_allergy": ("알레르기", "allergy", "유당", "유제품", "우유", "계란", "달걀", "견과", "갑각류", "밀", "대두"),
    "dairy_allergy": ("유당", "유제품", "우유", "milk", "dairy", "치즈", "요거트", "요구르트"),
    "egg_allergy": ("계란", "달걀", "egg"),
    "nut_allergy": ("견과", "땅콩", "peanut", "nut"),
    "shellfish_allergy": ("갑각류", "새우", "shellfish", "shrimp"),
    "wheat_allergy": ("밀", "wheat", "gluten"),
    "soy_allergy": ("대두", "soy"),
    "vegetarian": ("채식", "vegetarian"),
    "vegan": ("비건", "vegan"),
    "obesity": ("비만", "bmi", "체질량", "obesity"),
    "low_time": ("바빠", "시간", "8분", "10분", "15분", "짧"),
    "extreme_diet_risk": ("900kcal", "굶", "단식", "일주일에 7kg", "극단"),
}

_NEGATED_CONSTRAINT_PATTERNS = (
    ("hypertension", _negated_pattern(r"고혈압|혈압|hypertension")),
    ("diabetes", _negated_pattern(r"당뇨|혈당|diabetes|glucose")),
    ("cardiovascular_disease", _negated_pattern(r"심혈관|심장|협심증|cardiovascular|heart disease")),
    ("asthma", _negated_pattern(r"천식|asthma")),
    ("arthritis", _negated_pattern(r"관절염|arthritis")),
    ("knee_pain", _negated_pattern(r"무릎|knee", r"(?:통증|부상|pain)?")),
    ("back_pain", _negated_pattern(r"허리|요통|back|sciatica", r"(?:통증|부상|pain)?")),
    ("shoulder_pain", _negated_pattern(r"어깨|shoulder", r"(?:통증|부상|pain)?")),
    ("wrist_pain", _negated_pattern(r"손목|wrist", r"(?:통증|부상|pain)?")),
    ("ankle_pain", _negated_pattern(r"발목|ankle", r"(?:통증|부상|pain)?")),
    ("dairy_allergy", _negated_pattern(r"유제품|유당|우유|dairy|milk", r"알레르기?")),
    ("egg_allergy", _negated_pattern(r"달걀|계란|egg", r"알레르기?")),
    ("nut_allergy", _negated_pattern(r"견과류|견과|땅콩|nut|peanut", r"알레르기?")),
    ("shellfish_allergy", _negated_pattern(r"갑각류|새우|shellfish|shrimp", r"알레르기?")),
    ("wheat_allergy", _negated_pattern(r"밀|wheat|gluten", r"알레르기?")),
    ("soy_allergy", _negated_pattern(r"대두|soy", r"알레르기?")),
)

_SPECIALIZED_TOPIC_KEYWORDS = (
    "hiit",
    "인터벌",
    "근비대",
    "볼륨",
    "세트",
    "심박",
    "유산소",
    "스트레칭",
    "pnf",
    "가동성",
    "단백질",
    "크레아틴",
    "보충제",
    "오메가",
    "당뇨",
    "혈당",
    "고혈압",
    "심혈관",
    "천식",
    "관절염",
    "비만",
    "bmi",
    "dash",
    "알레르기",
    "유당",
    "채식",
    "비건",
    "vegetarian",
    "vegan",
    "무릎",
    "허리",
    "어깨",
    "손목",
)
