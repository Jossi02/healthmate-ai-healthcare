"""Persona node for tone polishing and final response generation."""
from __future__ import annotations

import json
import logging
import re
import time

from pydantic import BaseModel, Field

from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.core.persona_registry import resolve_persona
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)


class PersonaResponse(BaseModel):
    response: str = Field(description="Final message shown to the user")


def _selected_persona_id(profile: dict) -> str | None:
    persona_id = profile.get("selected_ai_persona")
    if isinstance(persona_id, str) and persona_id.strip():
        return persona_id.strip()
    return None


def _dedupe_repeated_sentences(text: str) -> str:
    chunks = [
        chunk.strip()
        for chunk in re.split(r"(?<=[.!?])\s+|\n+", text)
        if chunk.strip()
    ]
    if not chunks:
        return text.strip()

    normalized_seen: set[str] = set()
    deduped: list[str] = []
    for chunk in chunks:
        normalized = re.sub(r"\s+", " ", chunk).strip().lower()
        if normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        deduped.append(chunk)

    if len(deduped) == 1:
        return deduped[0]
    return "\n".join(deduped)


def _is_plan_flow_intent(intent: str) -> bool:
    return intent in {"계획", "수정", "계획_승인"}


_PERSONA_MARKERS = {
    "cheer_sis": ("좋아", "잘하고 있어", "충분해"),
    "soft_senior": ("괜찮아", "천천히", "부담"),
    "strict_trainer": ("핵심", "바로", "오늘은"),
    "science_coach": ("근거", "이유", "따라서"),
    "playful_buddy": ("오케이", "가볍게", "같이"),
    "daily_manager": ("정리하면", "체크", "계획"),
}

_PERSONA_OPENERS = {
    "cheer_sis": "좋아, 지금 방향 잘 잡고 있어요.",
    "soft_senior": "괜찮아요, 천천히 가도 됩니다.",
    "strict_trainer": "핵심만 바로 갈게요. 오늘은 이 순서입니다.",
    "science_coach": "근거와 이유를 보면,",
    "playful_buddy": "오케이, 가볍게 같이 가보자.",
    "daily_manager": "정리하면,",
}

_PERSONA_STYLE_LOCKS = {
    "cheer_sis": (
        "밝은 여성형 응원 누나 말투를 쓴다.",
        "존댓말을 유지하고 문장 끝은 주로 '-해요', '-할게요', '-좋아요'로 둔다.",
        "PT쌤식 명령형, 비서식 보고체, 친구식 반말을 섞지 않는다.",
    ),
    "soft_senior": (
        "차분한 선배의 존댓말을 쓴다.",
        "문장 끝은 '-됩니다', '-해도 됩니다', '-좋습니다'처럼 안정적으로 둔다.",
        "과한 응원, 장난, 명령형 반말을 피한다.",
    ),
    "strict_trainer": (
        "친한 PT쌤의 짧은 반말을 쓴다.",
        "문장 끝은 주로 '-해', '-가', '-멈춰', '-보자'처럼 행동 지시 중심으로 둔다.",
        "존댓말, 누나 말투, 비서식 보고체를 섞지 않는다.",
    ),
    "science_coach": (
        "남성형 분석 코치의 담백한 존댓말을 쓴다.",
        "문장 끝은 '-입니다', '-습니다'를 중심으로 하고, 근거 라벨을 짧게 붙인다.",
        "감성 응원, 장난, 명령형 반말을 피한다.",
    ),
    "playful_buddy": (
        "친구 같은 운동 메이트의 반말을 쓴다.",
        "문장 끝은 '-하자', '-가자', '-괜찮아', '-보자'처럼 가볍게 둔다.",
        "존댓말, 비서식 보고체, PT쌤식 압박을 섞지 않는다.",
    ),
    "daily_manager": (
        "비서/생활 매니저의 정돈된 존댓말을 쓴다.",
        "문장 끝은 '-했습니다', '-입니다', '-확인했습니다'처럼 보고체로 둔다.",
        "친구식 반말, 장난, 과한 감정 표현을 피한다.",
    ),
}


def _has_persona_marker(text: str, persona_id: str) -> bool:
    markers = _PERSONA_MARKERS.get(persona_id, ())
    normalized = text.lower()
    return any(marker.lower() in normalized for marker in markers)


def _looks_mostly_english(text: str) -> bool:
    letters = re.findall(r"[A-Za-z]", text)
    korean = re.findall(r"[가-힣]", text)
    return len(letters) > 30 and len(letters) > len(korean)


def _apply_persona_signature(text: str, persona_id: str, state: GraphState) -> str:
    if not text.strip():
        return text
    if _has_persona_marker(text, persona_id) and not _looks_mostly_english(text):
        return text

    # Persona should affect wording, not prepend a separate preamble.
    # Keep the user's requested result-first structure intact.
    if not _should_prepend_persona_signature(state):
        return text

    opener = _PERSONA_OPENERS.get(persona_id)
    if not opener:
        return text

    intent = str(state.get("intent") or "")
    if intent == "계획_승인":
        approval_openers = {
            "cheer_sis": "좋아, 저장 흐름까지 챙길게요.",
            "soft_senior": "괜찮아요, 이대로 반영할게요.",
            "strict_trainer": "확인. 이대로 반영합니다.",
            "science_coach": "확인했습니다. 계획대로 반영합니다.",
            "playful_buddy": "좋아, 이대로 같이 가보자.",
            "daily_manager": "체크했어요. 이대로 반영합니다.",
        }
        opener = approval_openers.get(persona_id, opener)

    if text.startswith(opener):
        return text
    return f"{opener}\n{text}"


def _should_prepend_persona_signature(state: GraphState) -> bool:
    return False


def _persona_guardrails(state: GraphState, draft_components: dict) -> str:
    intent = state.get("intent", "")
    lines = [
        "Global constraints:",
        "- Preserve the draft's main conclusion and factual scope.",
        "- The final response must be in Korean.",
        "- Start with the result itself. Do not add greeting, rapport-building, meta setup, or a persona catchphrase before the result.",
        "- Use this default order: result first, then at most 1-2 short reasons, then a short next action or approval question.",
        "- If the result is long, summarize representative items instead of expanding every detail in prose.",
        "- Keep the response concise in the persona's style; do not expand into broader general advice.",
        "- Do not weaken or generalize specific reasoning that is already present in reason_points.",
        "- If approval_question exists, keep that approval flow in the final response.",
    ]
    if state.get("support_mode") == "care":
        lines.append("- Keep the tone warm and validating, but do not change the task outcome or factual content.")

    if intent == "정보":
        lines.extend(
            [
                "- For info answers, the first sentence must directly answer the user's question.",
                "- Keep the answer focused on the asked point instead of widening into a generic wellness lecture.",
            ]
        )

    if intent in {"계획", "수정"}:
        lines.extend(
            [
                "- For plan or modify answers, preserve the plan direction and change axes already present in the draft.",
                "- If the draft refers to frequency, intensity, sets, rest, calories, ingredient changes, or meal composition, do not blur those specifics.",
                "- If plan_preview exists, keep the visible plan structure and major item details in the final response.",
                "- Keep the visible answer mostly to core_message, plan_preview, essential safety_notes, and approval_question.",
                "- Do not add profile rationale or explanatory phrases such as '알레르기 고려', '질환 고려', '제약 반영', '대체', or '제외' unless they are already in essential safety_notes.",
                "- Do not repeat the same plan summary, confirmation sentence, or approval request in multiple phrasings.",
                "- Keep the closing line to a single short next-step or confirmation sentence.",
            ]
        )

    if intent == "怨꾪쉷_?뱀씤":
        lines.extend(
            [
                "- Keep approval answers short and final.",
                "- Do not restate the full plan summary again once the user has already approved it.",
            ]
        )

    if draft_components.get("core_message"):
        lines.append(
            f"- The final response must stay semantically aligned with this core message: {draft_components['core_message']}"
        )

    if _is_plan_flow_intent(str(intent or "")):
        lines.extend(
            [
                "- Never repeat the same confirmation or plan summary in slightly different wording.",
                "- Keep plan-flow answers compact; avoid filler before or after the main point.",
            ]
        )

    if intent == "계획_승인":
        lines.extend(
            [
                "- Approval replies should be one short confirmation, not a fresh explanation.",
                "- Do not re-list the plan contents after approval unless the draft explicitly requires it.",
            ]
        )

    return "\n".join(lines)


def _persona_style_guardrails(persona_id: str) -> str:
    style_locks = _PERSONA_STYLE_LOCKS.get(persona_id)
    if not style_locks:
        return ""
    lines = ["Persona style lock:"]
    lines.extend(f"- {line}" for line in style_locks)
    return "\n".join(lines)


def make_persona_node(deps: NodeDeps):
    async def persona_node(state: GraphState) -> dict:
        if state.get("response"):
            return {}

        started_at = time.perf_counter()
        draft_components = normalize_draft_components(
            state.get("draft_components"),
            fallback_text=state.get("draft_response"),
        )
        draft_response = render_draft_preview(draft_components)

        profile = state.get("user_profile") or {}
        mbti = profile.get("mbti", "unknown")
        selected_persona = _selected_persona_id(profile)
        resolved_persona_id, persona_path = resolve_persona(selected_persona)
        intimacy_level = state.get("intimacy_level", 1)

        emotion = state.get("emotion") or {}
        emotion_label = emotion.get("label", "neutral")
        emotion_intensity = float(emotion.get("intensity", 0))
        emotion_str = f"{emotion_label} (intensity {emotion_intensity:.1f})"
        deps.trace.record_current_event(
            stage="persona",
            status="info",
            title="Persona polishing started",
            detail={
                "selected_persona_id": selected_persona,
                "resolved_persona_id": resolved_persona_id,
                "emotion": emotion_label,
            },
        )

        try:
            template = persona_path.read_text(encoding="utf-8")
            persona_prompt = template.format(
                persona_id=resolved_persona_id,
                emotion=emotion_str,
                mbti=mbti,
                intimacy_level=intimacy_level,
            )
            system_prompt = "\n\n".join(
                part
                for part in (
                    persona_prompt,
                    _persona_guardrails(state, draft_components),
                    _persona_style_guardrails(resolved_persona_id),
                )
                if part
            )
        except Exception as exc:
            logger.error("Failed to load persona prompt: %s", exc)
            deps.trace.record_current_alert(
                severity="warning",
                message="Persona prompt load failed; using fallback prompt",
                detail={"error": str(exc), "resolved_persona_id": resolved_persona_id},
            )
            system_prompt = (
                "Rewrite the structured draft naturally without changing facts.\n\n"
                + _persona_guardrails(state, draft_components)
            )

        structured_payload = {
            "core_message": draft_components["core_message"],
            "reason_points": draft_components["reason_points"],
            "suggested_action": draft_components["suggested_action"],
            "plan_preview": draft_components["plan_preview"],
            "safety_notes": draft_components["safety_notes"],
            "approval_question": draft_components["approval_question"],
            "search_grounding_summary": draft_components["search_grounding_summary"],
        }
        user_content = "[Structured Draft]\n" + json.dumps(
            structured_payload,
            ensure_ascii=False,
            indent=2,
        )

        try:
            raw = await deps.router.generate(
                system_prompt=system_prompt,
                user_content=user_content,
                response_schema=PersonaResponse,
            )
            result = PersonaResponse.model_validate_json(raw)
            final_response = result.response
        except Exception as exc:
            logger.error("Persona generation failed: %s", exc)
            deps.trace.record_current_alert(
                severity="warning",
                message="Persona generation failed; using draft preview",
                detail={"error": str(exc), "resolved_persona_id": resolved_persona_id},
            )
            final_response = draft_response

        if _looks_mostly_english(final_response):
            deps.trace.record_current_alert(
                severity="warning",
                message="Persona generation returned mostly English; using draft preview",
                detail={"resolved_persona_id": resolved_persona_id},
            )
            final_response = draft_response

        if state.get("intent") in {"怨꾪쉷", "?섏젙", "怨꾪쉷_?뱀씤"}:
            final_response = _dedupe_repeated_sentences(final_response)

        if _is_plan_flow_intent(str(state.get("intent") or "")):
            final_response = _dedupe_repeated_sentences(final_response)

        final_response = _apply_persona_signature(final_response, resolved_persona_id, state)

        deps.trace.record_current_event(
            stage="persona",
            status="ok",
            title="Persona polishing completed",
            detail={"resolved_persona_id": resolved_persona_id},
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )

        return {
            "response": final_response,
            "resolved_persona_id": resolved_persona_id,
        }

    return persona_node
