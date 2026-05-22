"""Safety response node for urgent-risk conversations."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)

_MENTAL_HEALTH_SAFETY_PATTERNS = re.compile(
    r"자해|자살|죽고\s*싶|극단적\s*선택|충동|해치고\s*싶|살고\s*싶지|살기\s*싫"
)
_PHYSICAL_SAFETY_PATTERNS = re.compile(
    r"가슴.*조여|가슴.*조이|가슴.*답답|가슴.*아파|흉통|명치.*답답|식은땀|식은\s*땀|"
    r"숨이?\s*차|호흡.*힘들|어지럽|어지러|쓰러질\s*것\s*같|실신|기절|"
    r"과다\s*복용|심한\s*통증|출혈|피가\s*멈추지"
)

_MENTAL_HEALTH_RESPONSE = (
    "지금은 운동이나 식단보다 즉시 사람과 연결되는 것이 먼저입니다.\n\n"
    "가까운 사람이나 보호자에게 지금 상태를 바로 알리고, "
    "자살예방상담전화 109 또는 정신건강상담전화 1577-0199에 즉시 연락하세요.\n"
    "당장 위험이 크면 119 또는 가까운 응급실로 바로 도움을 요청하세요.\n\n"
    "혼자 있지 말고, 위험한 물건이나 약물이 손에 닿지 않게 멀리하세요."
)

_PHYSICAL_EMERGENCY_RESPONSE = (
    "이건 운동 조언보다 즉시 응급 대응이 우선일 수 있는 증상입니다.\n\n"
    "지금 바로 운동을 멈추고 앉거나 누워 안정을 취하세요. "
    "증상이 계속되거나 심해지면 119에 연락하거나 가까운 응급실로 가야 합니다.\n"
    "혼자 이동하지 말고 가능하면 주변 사람에게 도움을 요청하세요.\n\n"
    "가슴 통증, 숨참, 심한 어지럼, 의식 저하, 과다 복용 의심이 있으면 "
    "운동을 다시 하지 말고 바로 진료를 받으세요."
)
_EXTREME_DIET_SAFETY_PATTERNS = re.compile(
    r"굶는?\s*식단|굶어서|단식.*살|물만\s*마시|물만.*식단|"
    r"일주일.*[5-9]\s*kg|[5-9]\s*kg.*일주일|[5-9]\s*kg.*빨리|빨리.*[5-9]\s*kg|"
    r"극단적.*다이어트|초저칼로리|(?:[1-9]\d{2}|1000)\s*(?:kcal|칼로리)"
)
_EXTREME_DIET_RESPONSE = (
    "식사를 거르거나 단기간에 큰 폭으로 감량하는 방식은 안전하지 않아서 도와드릴 수 없어요.\n\n"
    "극단적인 제한은 어지럼, 폭식 반동, 근손실, 컨디션 저하 위험을 키울 수 있습니다. "
    "감량은 식사를 유지하면서 작은 칼로리 조정과 활동량 조절로 가는 편이 안전합니다.\n\n"
    "최근 어지럼, 실신감, 폭식/절식 반복, 월경 이상, 복용약이나 질환이 있으면 전문가 상담을 우선하세요."
)

_ESCALATION_INTENSITY_THRESHOLD = 0.8


def make_safety_node(deps: NodeDeps):
    async def safety_node(state: GraphState) -> dict:
        emotion = state.get("emotion") or {}
        intensity = emotion.get("intensity", 0.0)
        user_id = state["user_id"]
        message = state["user_message"]
        safety_kind = _classify_safety_kind(message)
        profile = state.get("user_profile") or {}
        response = _response_for_safety_kind(safety_kind, message, profile)
        profile_note = _profile_safety_context(profile, safety_kind)
        if profile_note:
            response = f"{response}\n\n{profile_note}"

        logger.warning(
            "SAFETY_BLOCK | user_id=%s | kind=%s | message=%r | emotion_intensity=%.2f | timestamp=%s",
            user_id,
            safety_kind,
            message[:100],
            intensity,
            datetime.utcnow().isoformat(),
        )

        if intensity >= _ESCALATION_INTENSITY_THRESHOLD:
            logger.critical(
                "SAFETY_ESCALATION | user_id=%s | kind=%s | intensity=%.2f",
                user_id,
                safety_kind,
                intensity,
            )

        return {
            "response": response,
        }

    return safety_node


def _classify_safety_kind(message: str) -> str:
    if _MENTAL_HEALTH_SAFETY_PATTERNS.search(message):
        return "mental_health_crisis"
    if _EXTREME_DIET_SAFETY_PATTERNS.search(message):
        return "extreme_diet"
    if _PHYSICAL_SAFETY_PATTERNS.search(message):
        return "physical_emergency"
    return "physical_emergency"


def _response_for_safety_kind(safety_kind: str, message: str = "", profile: dict | None = None) -> str:
    profile = profile or {}
    if safety_kind == "mental_health_crisis":
        return _mental_health_response(message, profile)
    if safety_kind == "extreme_diet":
        return _extreme_diet_response(message, profile)
    return _physical_emergency_response(message, profile)


def _mental_health_response(message: str, profile: dict) -> str:
    normalized = message.lower()
    extra = ""
    if any(marker in normalized for marker in ("혼자", "아무도", "alone")):
        extra = "\n\n지금 혼자 있다면 문을 잠그고 버티기보다, 바로 연락 가능한 사람에게 현재 위치와 상태를 알려주세요."
    elif profile.get("emotional_context"):
        extra = "\n\n프로필의 감정 맥락도 함께 보면, 지금은 계획을 세우기보다 안전하게 연결되는 쪽이 우선이에요."
    return _MENTAL_HEALTH_RESPONSE + extra


def _physical_emergency_response(message: str, profile: dict) -> str:
    normalized = message.lower()
    if any(marker in normalized for marker in ("가슴", "숨", "호흡", "chest", "breath")):
        lead = "가슴 답답함이나 숨참은 운동 강도 문제가 아니라 응급 신호일 수 있어요."
    elif any(marker in normalized for marker in ("어지럽", "쓰러", "실신", "기절", "dizzy", "faint")):
        lead = "어지럼이나 쓰러질 것 같은 느낌은 바로 멈춰서 확인해야 하는 신호예요."
    elif any(marker in normalized for marker in ("출혈", "피가", "통증", "bleed", "pain")):
        lead = "심한 통증이나 출혈은 참고 운동할 상황이 아니에요."
    elif any(marker in normalized for marker in ("과다", "과복용", "복용", "overdose")):
        lead = "약물 과다 복용이 의심되면 운동 조언보다 즉시 응급 대응이 먼저예요."
    else:
        lead = "이건 운동 조언보다 즉시 응급 대응이 우선일 수 있는 증상입니다."

    conditions = _as_text_list(profile.get("medical_conditions") or profile.get("conditions"))
    condition_note = ""
    if conditions:
        condition_note = f"\n\n프로필에 질환 정보({', '.join(conditions)})가 있어 증상이 가볍게 느껴져도 낮게 보지 않는 편이 안전합니다."

    return (
        f"{lead}\n\n"
        "지금 바로 운동을 멈추고 앉거나 누워 안정을 취하세요. "
        "증상이 계속되거나 심해지면 119에 연락하거나 가까운 응급실로 가야 합니다.\n"
        "혼자 이동하지 말고 가능하면 주변 사람에게 도움을 요청하세요.\n\n"
        "가슴 통증, 숨참, 심한 어지럼, 의식 저하, 과다 복용 의심이 있으면 "
        "운동을 다시 하지 말고 바로 진료를 받으세요."
        f"{condition_note}"
    )


def _extreme_diet_response(message: str, profile: dict) -> str:
    normalized = message.lower()
    if re.search(r"(?:[1-9]\d{2}|1000)\s*(?:kcal|칼로리)", normalized):
        lead = "초저칼로리처럼 숫자로 식사를 과하게 제한하는 방식은 안전하지 않아요."
    elif re.search(r"[5-9]\s*kg", normalized):
        lead = "단기간에 큰 폭으로 감량하려는 방식은 몸에 부담이 커서 도와드릴 수 없어요."
    elif any(marker in normalized for marker in ("물만", "굶", "단식")):
        lead = "물만 마시거나 굶는 방식은 안전하지 않아서 식단으로 제안할 수 없어요."
    else:
        lead = "극단적인 다이어트 방식은 안전하지 않아서 도와드릴 수 없어요."

    notes = [
        "극단적인 제한은 어지럼, 폭식 반동, 근손실, 컨디션 저하 위험을 키울 수 있습니다.",
        "감량은 식사를 유지하면서 작은 칼로리 조정, 단백질과 채소가 있는 균형 식사, 활동량 조절로 가는 편이 안전합니다.",
        "최근 어지럼, 실신감, 폭식/절식 반복, 월경 이상, 복용약이나 질환이 있으면 전문가 상담을 우선하세요.",
    ]
    age = _safe_int(profile.get("age"))
    if age and age < 19:
        notes.append("성장기에는 체중 감량 목표가 있어도 굶기나 급격한 제한은 특히 피해야 합니다.")
    emotion_text = str(profile.get("emotional_context") or "").lower()
    if any(marker in emotion_text for marker in ("desperate", "body image", "불안", "절박", "스트레스")):
        notes.append("지금 압박감이 큰 상태라면 목표를 더 세게 잡기보다 안전하게 낮추는 것이 먼저입니다.")

    return f"{lead}\n\n" + "\n".join(f"- {note}" for note in notes)


def _profile_safety_context(profile: dict, safety_kind: str = "") -> str:
    constraints: list[str] = []
    for key in ("injury_history", "medical_conditions", "conditions", "pain_points", "allergies", "dietary_restrictions"):
        value = profile.get(key)
        if isinstance(value, list):
            constraints.extend(str(item).strip() for item in value if str(item).strip())
        elif value:
            constraints.append(str(value).strip())

    parts: list[str] = []
    if profile.get("age"):
        parts.append(f"나이 {profile['age']}세")
    if profile.get("exercise_level") or profile.get("activity_level"):
        parts.append(f"운동 수준 {profile.get('exercise_level') or profile.get('activity_level')}")
    if constraints:
        parts.append(f"제약({', '.join(constraints)})")
    if not parts:
        return ""
    if safety_kind == "extreme_diet":
        return "프로필 기준으로도 " + ", ".join(parts) + "이 확인되므로 급격한 제한보다 균형 식사와 전문가 확인을 우선하세요."
    if safety_kind == "mental_health_crisis":
        return "프로필 기준으로도 " + ", ".join(parts) + "이 확인되지만, 지금은 운동/식단 조정보다 즉시 도움 연결이 우선입니다."
    return "프로필 기준으로도 " + ", ".join(parts) + "이 확인되므로 무리하지 말고 전문가 확인 전에는 강한 운동을 피하세요."


def _as_text_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _safe_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
