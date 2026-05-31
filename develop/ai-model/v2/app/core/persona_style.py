"""Persona style helpers used inside generation/finalization."""
from __future__ import annotations

import re

from app.schemas.state import GraphState


def selected_persona_id(profile: dict) -> str | None:
    persona_id = profile.get("selected_ai_persona")
    if isinstance(persona_id, str) and persona_id.strip():
        return persona_id.strip()
    return None


def dedupe_repeated_sentences(text: str) -> str:
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


def normalize_plan_flow_preview(
    text: str,
    state: GraphState,
    draft_components: dict,
    persona_id: str,
) -> str:
    plan_preview = str(draft_components.get("plan_preview") or "").strip()
    if not plan_preview or not _is_plan_flow_intent(str(state.get("intent") or "")):
        return text

    approval_question = _persona_plan_approval_question(
        str(draft_components.get("approval_question") or "").strip(),
        state,
        persona_id,
    )
    first_line = _first_nonempty_line(text)
    if not first_line or first_line.startswith("-") or (approval_question and approval_question in first_line):
        first_line = str(draft_components.get("core_message") or "").strip()
    first_line = _persona_plan_core_line(first_line, state, persona_id)

    if _first_nonempty_line(text).startswith("-"):
        lines = [plan_preview]
    else:
        lines = [line for line in (first_line, plan_preview) if line]
    lines.extend(str(note).strip() for note in (draft_components.get("safety_notes") or [])[:2] if str(note).strip())
    if approval_question:
        lines.append(approval_question)
    return "\n".join(lines).strip() or text


def strip_plan_flow_preamble(text: str, state: GraphState) -> str:
    if not _is_plan_flow_intent(str(state.get("intent") or "")):
        return text

    lines = [line.rstrip() for line in str(text or "").splitlines()]
    removed = 0
    while removed < len(lines):
        compact = _compact_for_visibility(lines[removed])
        if not compact:
            removed += 1
            continue
        if any(marker in compact for marker in ("죄송", "잘못이해", "설명드릴", "설명해드릴", "먼저말씀")):
            removed += 1
            continue
        break

    if removed <= 0 or removed >= len(lines):
        return text
    return "\n".join(lines[removed:]).strip()


def apply_persona_signature(text: str, persona_id: str, state: GraphState) -> str:
    if not text.strip():
        return text
    if _has_persona_marker(text, persona_id) and not looks_mostly_english(text):
        return text

    # Keep result-first UX. Persona prompts should shape wording during generation.
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


def persona_style_guardrails(persona_id: str) -> str:
    style_locks = _PERSONA_STYLE_LOCKS.get(persona_id)
    if not style_locks:
        return ""
    lines = ["Persona style lock:"]
    lines.extend(f"- {line}" for line in style_locks)
    return "\n".join(lines)


def persona_guardrails(state: GraphState, draft_components: dict) -> str:
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
        "- For plan corrections, do not start with apology or explanation. Start with the corrected result.",
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


def looks_mostly_english(text: str) -> bool:
    letters = re.findall(r"[A-Za-z]", text)
    korean = re.findall(r"[가-힣]", text)
    return len(letters) > 30 and len(letters) > len(korean)


def _is_plan_flow_intent(intent: str) -> bool:
    return intent in {"계획", "수정", "계획_승인"}


def _persona_plan_core_line(line: str, state: GraphState, persona_id: str) -> str:
    domain = _plan_domain_label(state)
    scope = _plan_scope_label(state)
    intent = str(state.get("intent") or "")
    if persona_id == "strict_trainer":
        return f"{scope}{domain} 플랜으로 고쳤어." if intent == "수정" else f"{scope}{domain} 플랜이야."
    if persona_id == "playful_buddy":
        return f"{scope}{domain} 플랜으로 다시 잡았어." if intent == "수정" else f"{scope}{domain} 플랜 잡아봤어."
    if persona_id == "daily_manager":
        return f"{scope}{domain} 플랜을 수정했습니다." if intent == "수정" else f"{scope}{domain} 플랜을 정리했습니다."
    if persona_id == "science_coach":
        return f"{scope}{domain} 플랜을 수정했습니다." if intent == "수정" else f"{scope}{domain} 플랜입니다."
    if persona_id == "soft_senior":
        return f"{scope}{domain} 플랜으로 조정했습니다." if intent == "수정" else f"{scope}{domain} 플랜을 제안드립니다."
    if persona_id == "cheer_sis":
        return f"{scope}{domain} 플랜으로 맞춰봤어요." if intent == "수정" else f"{scope}{domain} 플랜을 제안해요."
    return line


def _persona_plan_approval_question(question: str, state: GraphState, persona_id: str) -> str:
    if not question:
        return question
    domain = _plan_domain_label(state)
    intent = str(state.get("intent") or "")
    if persona_id == "strict_trainer":
        return f"이 {domain} 플랜으로 갈까?"
    if persona_id == "playful_buddy":
        return f"이 {domain} 플랜으로 가볼까?"
    if persona_id == "daily_manager":
        return f"이 {domain} 플랜으로 {'수정할까요' if intent == '수정' else '작성할까요'}?"
    if persona_id == "science_coach":
        return f"이 {domain} 플랜으로 {'수정할까요' if intent == '수정' else '작성할까요'}?"
    if persona_id == "soft_senior":
        return f"이 {domain} 플랜으로 {'조정할까요' if intent == '수정' else '작성할까요'}?"
    if persona_id == "cheer_sis":
        return f"이 {domain} 플랜으로 {'수정할까요' if intent == '수정' else '작성할까요'}?"
    return question


def _plan_domain_label(state: GraphState) -> str:
    domain = (
        state.get("proposed_plan_type")
        or state.get("modify_target")
        or (state.get("active_proposal") or {}).get("domain")
        or state.get("domain")
    )
    return "식단" if domain == "diet" else "운동"


def _plan_scope_label(state: GraphState) -> str:
    message = str(state.get("user_message") or "").replace(" ", "")
    if any(marker in message for marker in ("일주일", "한주", "1주", "7일")):
        return "일주일 "
    if "한달" in message or "1달" in message or "1개월" in message or "30일" in message:
        return "한 달 "
    if "오늘" in message:
        return "오늘 "
    return ""


def _first_nonempty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _compact_for_visibility(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _has_persona_marker(text: str, persona_id: str) -> bool:
    markers = _PERSONA_MARKERS.get(persona_id, ())
    normalized = text.lower()
    return any(marker.lower() in normalized for marker in markers)


def _should_prepend_persona_signature(state: GraphState) -> bool:
    return False


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
