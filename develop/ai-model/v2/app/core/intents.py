"""Shared intent labels and helpers for LangGraph routing."""
from __future__ import annotations

INTENT_CARE = "공감_케어"
INTENT_PLAN = "계획"
INTENT_MODIFY = "수정"
INTENT_APPROVAL = "계획_승인"
INTENT_RECORD = "기록"
INTENT_INFO = "정보"
INTENT_FALLBACK = "fallback"
INTENT_CASUAL = "casual"
INTENT_SAFETY = "안전경고"
INTENT_HOME_RECOMMENDATION = "home_recommendation"

KNOWN_INTENTS = {
    INTENT_CARE,
    INTENT_PLAN,
    INTENT_MODIFY,
    INTENT_APPROVAL,
    INTENT_RECORD,
    INTENT_INFO,
    INTENT_FALLBACK,
    INTENT_CASUAL,
    INTENT_SAFETY,
    INTENT_HOME_RECOMMENDATION,
}

_INTENT_ALIASES = {
    "care": INTENT_CARE,
    "empathy": INTENT_CARE,
    "support": INTENT_CARE,
    "plan": INTENT_PLAN,
    "create_plan": INTENT_PLAN,
    "create": INTENT_PLAN,
    "modify": INTENT_MODIFY,
    "update_plan": INTENT_MODIFY,
    "edit": INTENT_MODIFY,
    "approval": INTENT_APPROVAL,
    "approve": INTENT_APPROVAL,
    "confirm": INTENT_APPROVAL,
    "record": INTENT_RECORD,
    "log": INTENT_RECORD,
    "info": INTENT_INFO,
    "question": INTENT_INFO,
    "qa": INTENT_INFO,
    "safety": INTENT_SAFETY,
    "warning": INTENT_SAFETY,
    "home": INTENT_HOME_RECOMMENDATION,
    "home_recommendation": INTENT_HOME_RECOMMENDATION,
}


def normalize_intent(value: object, *, fallback: str = INTENT_FALLBACK) -> str:
    """Return a canonical intent label from model or rule-based output."""
    text = str(value or "").strip()
    if text in KNOWN_INTENTS:
        return text

    key = text.lower().replace(" ", "_").replace("-", "_")
    return _INTENT_ALIASES.get(key, fallback)
