"""Helpers for the structured Draft -> Persona contract."""
from __future__ import annotations

from typing import Any

from app.schemas.state import DraftComponents


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text:
            cleaned.append(text)
    return cleaned


def _compact_line(value: str, max_chars: int = 120) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def normalize_draft_components(
    payload: dict[str, Any] | None,
    fallback_text: str | None = None,
) -> DraftComponents:
    if not payload:
        text = _clean_text(fallback_text) or "무엇을 도와드릴까요?"
        return {
            "core_message": _compact_line(text, 180),
            "reason_points": [],
            "suggested_action": "",
            "plan_preview": "",
            "safety_notes": [],
            "approval_question": None,
            "search_grounding_summary": "",
        }

    core_message = (
        _clean_text(payload.get("core_message"))
        or _clean_text(fallback_text)
        or "무엇을 도와드릴까요?"
    )
    approval_question = _clean_text(payload.get("approval_question")) or None

    return {
        "core_message": _compact_line(core_message, 180),
        "reason_points": [
            _compact_line(item)
            for item in _clean_list(payload.get("reason_points"))[:2]
        ],
        "suggested_action": _compact_line(
            _clean_text(payload.get("suggested_action")),
            140,
        ),
        "plan_preview": _clean_text(payload.get("plan_preview")),
        "safety_notes": [
            _compact_line(item, 140)
            for item in _clean_list(payload.get("safety_notes"))[:3]
        ],
        "approval_question": approval_question,
        "search_grounding_summary": _compact_line(
            _clean_text(payload.get("search_grounding_summary")),
            120,
        ),
    }


def render_draft_preview(components: DraftComponents) -> str:
    parts: list[str] = []

    if components["core_message"]:
        parts.append(components["core_message"])

    if components["plan_preview"]:
        parts.append(f"결과:\n{components['plan_preview']}")

    if components["reason_points"]:
        reasons = "\n".join(f"- {item}" for item in components["reason_points"])
        parts.append(f"근거:\n{reasons}")

    if components["search_grounding_summary"]:
        parts.append(f"근거 요약: {components['search_grounding_summary']}")

    if components["suggested_action"]:
        parts.append(f"다음 행동: {components['suggested_action']}")

    if components["safety_notes"]:
        notes = "\n".join(f"- {item}" for item in components["safety_notes"])
        parts.append(f"주의:\n{notes}")

    if components["approval_question"]:
        parts.append(components["approval_question"])

    return "\n\n".join(part for part in parts if part).strip()
