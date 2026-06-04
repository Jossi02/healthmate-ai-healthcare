"""Intent analysis node for Layer 2 routing."""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.core.conversation_state import infer_domain
from app.core.intents import (
    INTENT_APPROVAL,
    INTENT_CARE,
    INTENT_CASUAL,
    INTENT_FALLBACK,
    INTENT_HOME_RECOMMENDATION,
    INTENT_INFO,
    INTENT_MODIFY,
    INTENT_PLAN,
    INTENT_RECORD,
    INTENT_SAFETY,
    normalize_intent,
)
from app.core.prompt_loader import load_prompt
from app.graph.deps import NodeDeps
from app.schemas.intent import IntentOutput
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)

_SAFETY_PATTERNS = re.compile(
    r"자해|자살|죽고\s*싶|살고\s*싶지|살기\s*싫|극단적\s*선택|위험|실행|마약|과다\s*복용|과복용|"
    r"가슴.*조여|가슴.*조이|가슴.*아파|가슴.*답답|흉통|식은땀|명치.*답답|숨.*차|호흡.*힘들|어지럽|어지러|심한.*알레르기|"
    r"심한.*통증|출혈|피가.*멈추지|기절|"
    r"약을.*많이.*먹|굶는?\s*식단|굶어서|단식.*살|물만\s*마시|물만.*식단|"
    r"일주일.*[5-9]\s*kg|[5-9]\s*kg.*빨리|빨리.*[5-9]\s*kg|"
    r"[5-9]\s*kg.*일주일|극단적.*다이어트|초저칼로리|"
    r"(?:[1-9]\d{2}|1000)\s*(?:kcal|칼로리)",
    re.IGNORECASE,
)
_PHYSICAL_EMERGENCY_PATTERNS = re.compile(
    r"(운동|뛰|러닝|걷|스쿼트|헬스|유산소|근력|하다가|도중).*"
    r"(가슴|흉통|숨|호흡|식은땀|식은\s*땀|어지럽|어지러|실신|기절|쓰러질)|"
    r"(가슴|흉통|숨|호흡|식은땀|식은\s*땀|어지럽|어지러|실신|기절|쓰러질).*"
    r"(계속|해도\s*돼|해도\s*될|괜찮|운동|멈춰|중단|도중|하다가)",
    re.IGNORECASE,
)
_CASUAL_PATTERNS = re.compile(
    r"^(안녕|하이|헬로|hello|hi|반가워|고마워|감사|수고|잘가|bye)[\s!?.]*$",
    re.IGNORECASE,
)
_OFFTOPIC_PATTERNS = re.compile(
    r"주식|주가|코인|비트코인|투자|매수|매도|환율|부동산|로또|복권|"
    r"날씨|뉴스|정치|선거|맛집|영화\s*추천|드라마\s*추천|게임\s*추천|"
    r"코딩|파이썬|자바스크립트|번역|수학\s*문제|숙제",
    re.IGNORECASE,
)

_INTENT_SYSTEM_PROMPT = load_prompt("nodes/intent/system.md")

_PLAN_CONFIRMATION_SYSTEM_PROMPT = """You are a narrow classifier for plan confirmation in a health coaching chat.

Decide only whether the user's latest message approves the already proposed plan.

Return approved=true only when all of these are true:
- The assistant previously proposed or modified a plan and is waiting for confirmation.
- The user is accepting, confirming, applying, or proceeding with that existing plan.
- The user is not introducing any new change request, constraint, or modification.

Return approved=false when any of these are true:
- The user requests a new change, replacement, deletion, addition, or adjustment.
- The user asks a new question or goes off topic.
- The user expresses dislike/rejection without clearly approving the current plan.
- The message is ambiguous.
"""


class PlanConfirmationDecision(BaseModel):
    approved: bool = Field(description="Whether the user approved the currently proposed plan")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


_PLAN_DOMAIN_KEYWORDS = (
    "운동",
    "식단",
    "식사",
    "메뉴",
    "루틴",
    "플랜",
    "계획",
    "workout",
    "diet",
    "meal",
    "클라이밍",
    "러닝",
    "수영",
    "자전거",
    "걷기",
    "산책",
    "스트레칭",
    "헬스",
    "근육통",
    "허리",
    "무릎",
    "어깨",
    "통증",
    "휴식",
    "회복",
)
_PLAN_REQUEST_KEYWORDS = (
    "추천",
    "루틴",
    "플랜",
    "계획",
    "작성",
    "짜줘",
    "짜 줘",
    "세워줘",
    "세워 줘",
    "잡아줘",
    "잡아 줘",
    "잡아달",
    "구성",
    "설계",
    "만들어",
    "추천해줘",
    "정리해줘",
    "제안",
    "뭐 하면",
    "뭐하면",
    "하면 돼",
    "하면 되",
)
_PLAN_EXCLUDE_KEYWORDS = (
    "수정",
    "바꿔",
    "변경",
    "교체",
    "조정",
    "빼",
    "추가",
    "확인",
    "확정",
    "반영",
    "적용",
    "진행",
    "기록",
    "체크",
)
_NEW_PLAN_MARKERS = (
    "새로",
    "새로운",
    "새 계획",
    "새 플랜",
    "새 루틴",
    "다른 계획",
    "다른 플랜",
    "다른 루틴",
    "처음부터",
    "신규",
)
_MODIFY_KEYWORDS = (
    "수정",
    "바꿔",
    "변경",
    "교체",
    "조정",
    "빼",
    "다시",
    "줄여",
    "늘려",
    "덜",
    "추가",
    "제외",
    "대체",
    "제거",
    "짧게",
    "가볍게",
    "안전하게",
    "현실적으로",
)
_APPROVAL_KEYWORDS = (
    "확인",
    "확정",
    "반영",
    "적용",
    "진행",
    "진행하자",
    "이대로",
    "그대로",
    "좋아",
    "좋습니다",
    "오케이",
)
_APPROVAL_COMMITMENT_KEYWORDS = (
    "진행",
    "적용",
    "반영",
    "저장",
    "확정",
    "확인",
    "이대로",
    "그대로",
    "오케이",
    "좋아",
    "ok",
    "okay",
)
_EXPLICIT_PLAN_APPROVAL_PHRASES = (
    "좋아 진행해줘",
    "좋아 반영해줘",
    "좋아 적용해줘",
    "그대로 진행해줘",
    "그대로 적용해줘",
    "이 계획으로 진행해줘",
    "이 계획 반영해줘",
    "확정하고 반영해줘",
    "확인했어",
)
_PLAN_REFERENCE_KEYWORDS = (
    "계획",
    "플랜",
    "루틴",
    "식단",
    "운동",
    "방금",
    "제안",
    "추천",
    "수정안",
    "그거",
    "그걸",
    "이거",
)
_PROFILE_FIELD_KEYWORDS = (
    "체중",
    "몸무게",
    "키",
    "알레르기",
    "부상",
    "부상 이력",
    "기저질환",
    "질환",
    "복용약",
    "나이",
    "성별",
    "목표",
    "활동량",
    "mbti",
    "별명",
)
_PROFILE_UPDATE_KEYWORDS = (
    "기록",
    "추가",
    "수정",
    "변경",
    "반영",
    "업데이트",
    "입력",
    "저장",
)
_CONTEXT_DEPENDENT_REFERENCES = (
    "그거",
    "그걸",
    "그걸로",
    "아까 말한 거",
    "그 방식",
    "방금 거",
    "저거",
)
_MEMORY_SAVE_KEYWORDS = (
    "기억해줘",
    "기억해 줘",
    "기억해",
    "잊지마",
    "잊지 마",
    "앞으로 내 별명은",
    "내 별명은",
)
_MEMORY_QUERY_KEYWORDS = (
    "기억나",
    "기억해?",
    "내가 뭐라고 했",
    "방금 내가 말한",
    "아까 내가 말한",
    "내 별명",
    "예전에 말한",
    "이전에 말한",
    "전에 말한",
    "지난번에 말한",
    "저장한",
    "기억해둔",
    "내 취향",
    "내 선호",
    "조금 전에 말한",
)
_PLAN_CONFIRMATION_REFERENCE_KEYWORDS = (
    "아까",
    "방금",
    "그전",
    "이전",
    "그거",
    "그걸",
    "그 계획",
    "그 운동",
    "그 식단",
    "그대로",
)
_PLAN_CHANGE_MARKERS = (
    "말고",
    "대신",
    "바꿔",
    "변경",
    "조정",
    "빼",
    "줄여",
    "늘려",
    "추가",
    "제외",
    "강도",
    "세트",
    "횟수",
    "시간",
    "칼로리",
    "식사",
    "재료",
    "안전하게",
    "현실적으로",
    "짧게",
    "가볍게",
    "대체",
    "제거",
)
_SHORT_APPROVAL_RESPONSES = (
    "응",
    "네",
    "좋아",
    "좋아요",
    "좋습니다",
    "오케이",
    "okay",
    "ok",
)
_CARE_SUPPORT_MARKERS = (
    "지쳐",
    "지쳤",
    "힘들",
    "피곤",
    "컨디션",
    "잠을 못",
    "잠 못",
    "수면 부족",
    "불안",
    "우울",
    "무기력",
    "걱정",
    "스트레스",
    "멘탈",
    "버겁",
    "외롭",
    "외로워",
    "실패",
    "못 하겠",
    "못하겠",
    "하기 싫",
    "하기싫",
    "망쳐",
    "망했",
    "식욕",
    "폭식",
    "배고파",
    "뻐근",
    "부담",
    "겁나",
    "조급",
    "자신감",
    "할 수 있게",
    "해낼 수",
    "쉬어도",
    "쉬어야",
    "루틴이 망가",
)
_INFO_REQUEST_MARKERS = (
    "왜",
    "이유",
    "근거",
    "알려줘",
    "어떤",
    "뭘",
    "무엇",
    "피해야",
    "괜찮",
    "가능",
    "해야",
    "해도 돼",
    "해도 될",
    "될까",
    "좋을까",
    "하면 좋",
    "뭐부터",
    "먹지",
    "먹어도",
    "쉴까",
    "쉬어도",
    "쉬어야",
    "어떻게",
    "대신",
    "설명",
    "정리",
    "판단",
    "빠른지",
    "적절",
    "페이스",
    "기준",
    "맞는지",
)
_HEALTH_CONTEXT_KEYWORDS = (
    "컨디션",
    "피곤",
    "잠을 못",
    "잠 못",
    "수면",
    "식욕",
    "폭식",
    "배고",
    "뻐근",
    "근육통",
    "허리",
    "목",
    "손목",
    "무릎",
    "어깨",
    "통증",
    "몸",
    "회복",
    "휴식",
    "쉬어",
    "쉬고",
    "페이스",
    "강도",
    "속도",
    "루틴",
    "걷기",
    "산책",
    "스트레칭",
    "물",
    "수분",
    "아침",
    "점심",
    "저녁",
    "간식",
    "단백질",
    "먹지",
    "먹어도",
    "먹으면",
    "먹을까",
)


def make_intent_node(deps: NodeDeps):
    async def analyze_intent_node(state: GraphState) -> dict:
        if state.get("request_kind") == "home_recommendation":
            return _build_result(INTENT_HOME_RECOMMENDATION, state)

        message = str(state["user_message"])
        routing_message = _routing_message(state, message)
        previous_intent = state.get("previous_intent")
        signals = _intent_signal_snapshot(message, routing_message, state)

        if _looks_like_safety_request(message):
            return _build_result(INTENT_SAFETY, state)

        if _CASUAL_PATTERNS.match(message.strip()) and previous_intent != INTENT_CARE:
            return _build_result(INTENT_CASUAL, state)

        if _looks_like_memory_save_request(routing_message):
            return _build_result(
                INTENT_CASUAL,
                state,
                confidence=0.9,
                should_save_episode=True,
            )

        if _looks_like_memory_query(routing_message):
            if not _looks_like_short_term_memory_query(routing_message):
                return _build_result(
                    INTENT_INFO,
                    state,
                    confidence=0.92,
                    search_targets=["vdb_memory", "vdb_user_important"],
                    requires_past_memory=True,
                    short_term_memory_query=False,
                )
            return _build_result(
                INTENT_INFO,
                state,
                confidence=0.92,
                search_targets=[],
                requires_past_memory=False,
                short_term_memory_query=True,
            )

        if _looks_like_offtopic_request(routing_message):
            _record_intent_fallback(
                deps,
                reason="out_of_scope_non_health_request",
                signals=signals,
            )
            return _build_result(INTENT_FALLBACK, state, confidence=0.86)

        if _looks_like_context_dependent_fallback(message, state):
            _record_intent_fallback(
                deps,
                reason="unresolved_context_reference",
                signals=signals,
            )
            return _build_result(INTENT_FALLBACK, state, confidence=0.9)

        if _looks_like_context_setup(message) and not _looks_like_plan_request(routing_message):
            return _build_result(INTENT_CASUAL, state, confidence=0.82)

        if _looks_like_plan_delete_request(message) or _looks_like_plan_delete_request(routing_message):
            return _build_result(
                INTENT_RECORD,
                state,
                confidence=0.95,
                record_type="plan_delete",
                modify_target=_infer_plan_delete_target(routing_message or message),
            )

        if _looks_like_plan_check_record(message) or _looks_like_plan_check_record(routing_message):
            return _build_result(INTENT_RECORD, state, confidence=0.94, record_type="plan_check", is_today=True)

        if _looks_like_profile_record(message) or _looks_like_profile_record(routing_message):
            return _build_result(INTENT_RECORD, state, confidence=0.94, record_type="profile")

        if _looks_like_condition_info_question(message, routing_message):
            return _build_result(
                INTENT_INFO,
                state,
                confidence=0.91,
                search_targets=["vdb_external"],
            )

        if _looks_like_simple_condition_statement(message, routing_message):
            return _build_result(INTENT_CASUAL, state, confidence=0.88)

        if _looks_like_emotional_care_request(message, routing_message):
            return _build_result(INTENT_CARE, state, confidence=0.9)

        awaiting_plan_confirmation = _has_pending_plan_confirmation_v2(state)

        if awaiting_plan_confirmation and _looks_like_pending_plan_revision(message, routing_message, state):
            return _build_result(
                INTENT_MODIFY,
                state,
                confidence=0.96,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
                modify_target=_pending_plan_domain(state, routing_message),
            )

        if _looks_like_read_only_info_request(message, routing_message):
            return _build_result(
                INTENT_INFO,
                state,
                confidence=0.92,
                search_targets=["vdb_external"],
            )

        if _looks_like_ambiguous_mixed_plan_request(routing_message):
            _record_intent_fallback(
                deps,
                reason="ambiguous_mixed_plan_domain",
                signals=signals,
            )
            return _ambiguous_plan_clarification_result(state, signals)

        if _looks_like_mixed_plan_clarification_followup(message, state):
            result = _build_result(
                INTENT_PLAN,
                state,
                confidence=0.9,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
                domain_override="workout",
            )
            result["routing_diagnostics"] = {
                **(result.get("routing_diagnostics") or {}),
                "reason_codes": [
                    *((result.get("routing_diagnostics") or {}).get("reason_codes") or []),
                    "mixed_plan_followup_start_workout_first",
                ],
                "domain_ambiguous": False,
                "needs_clarification_recommended": False,
            }
            return result

        if _looks_like_pending_sequential_plan_followup(message, state):
            pending = state.get("pending_sequential_plan") or {}
            pending_domain = str(pending.get("domain") or "diet")
            result = _build_result(
                INTENT_PLAN,
                state,
                confidence=0.91,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
                domain_override=pending_domain,
            )
            result["routing_diagnostics"] = {
                **(result.get("routing_diagnostics") or {}),
                "reason_codes": [
                    *((result.get("routing_diagnostics") or {}).get("reason_codes") or []),
                    "pending_sequential_plan_followup",
                ],
                "domain_ambiguous": False,
                "needs_clarification_recommended": False,
            }
            return result

        if _looks_like_new_plan_request(message, routing_message):
            return _build_result(
                INTENT_PLAN,
                state,
                confidence=0.94,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
            )

        if (
            _looks_like_question_followup(routing_message)
            and not _looks_like_plan_request(routing_message)
            and not _matches_hardcoded_confirmation_approval(message)
        ):
            return _build_result(
                INTENT_INFO,
                state,
                confidence=0.9,
                search_targets=["vdb_external"],
            )

        if awaiting_plan_confirmation:
            deps.trace.record_current_event(
                stage="confirm_gate",
                status="info",
                title="Plan confirmation gate entered",
                detail={
                    "awaiting_plan_confirmation": True,
                    "has_proposed_plan": bool(state.get("proposed_plan")),
                    "proposed_plan_count": len(state.get("proposed_plan") or []),
                    "proposed_plan_type": state.get("proposed_plan_type"),
                    "proposed_plan_action": state.get("proposed_plan_action"),
                    "last_assistant_excerpt": _latest_assistant_message_v2(state)[:200],
                    "user_message": message,
                },
            )

        if awaiting_plan_confirmation and _looks_like_explicit_plan_change(message, state):
            return _build_result(
                INTENT_MODIFY,
                state,
                confidence=0.93,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
                modify_target=_pending_plan_domain(state, routing_message),
            )

        if awaiting_plan_confirmation and _matches_hardcoded_confirmation_approval(message):
            deps.trace.record_current_event(
                stage="confirm_gate",
                status="ok",
                title="Hardcoded confirmation approval matched",
                detail={"source": "hardcoded_phrase", "user_message": message},
            )
            return _build_result(INTENT_APPROVAL, state, confidence=0.99)

        if awaiting_plan_confirmation:
            decision = await _classify_plan_confirmation(deps, state, message)
            if decision is not None and decision.approved:
                return _build_result(
                    INTENT_APPROVAL,
                    state,
                    confidence=max(0.9, float(decision.confidence or 0.0)),
                )
            deps.trace.record_current_event(
                stage="confirm_gate",
                status="warn",
                title="Confirmation gate fell through to general routing",
                detail={
                    "user_message": message,
                    "decision_present": decision is not None,
                    "approved": bool(decision.approved) if decision is not None else None,
                    "confidence": float(decision.confidence or 0.0) if decision is not None else None,
                    "reason": decision.reason if decision is not None else "no_decision",
                },
            )

        if awaiting_plan_confirmation and _looks_like_explicit_plan_change(routing_message, state):
            return _build_result(
                INTENT_MODIFY,
                state,
                confidence=0.93,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
                modify_target=_pending_plan_domain(state, routing_message),
            )

        if not awaiting_plan_confirmation and _looks_like_plan_approval(routing_message, state):
            return _build_result(INTENT_APPROVAL, state, confidence=0.94)

        if (
            (_looks_like_care_request(routing_message) or _looks_like_care_request(message))
            and not _looks_like_info_request(routing_message)
            and not _looks_like_plan_request(routing_message)
            and not _looks_like_modify_request(routing_message)
        ):
            if _looks_like_simple_condition_statement(message, routing_message):
                return _build_result(INTENT_CASUAL, state, confidence=0.86)
            return _build_result(INTENT_CARE, state, confidence=0.88)

        if _looks_like_profile_record(routing_message):
            return _build_result(INTENT_RECORD, state, confidence=0.9)

        if _looks_like_modify_request(routing_message):
            if _looks_like_ambiguous_mixed_plan_request(routing_message):
                _record_intent_fallback(
                    deps,
                    reason="ambiguous_mixed_plan_domain",
                    signals=signals,
                )
                return _ambiguous_plan_clarification_result(state, signals)
            return _build_result(
                INTENT_MODIFY,
                state,
                confidence=0.92,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
                modify_target=_pending_plan_domain(state, routing_message),
            )

        if _looks_like_plan_request(routing_message):
            if _looks_like_ambiguous_mixed_plan_request(routing_message):
                _record_intent_fallback(
                    deps,
                    reason="ambiguous_mixed_plan_domain",
                    signals=signals,
                )
                return _ambiguous_plan_clarification_result(state, signals)
            return _build_result(
                INTENT_PLAN,
                state,
                confidence=0.92,
                search_targets=["vdb_external", "vdb_memory", "vdb_user_important", "web"],
            )

        if _looks_like_info_request(routing_message):
            return _build_result(
                INTENT_INFO,
                state,
                confidence=0.88,
                search_targets=["vdb_external"],
            )

        context = _build_context_v3(state)
        user_content = (
            f"{context}\n\n[Original User Message]\n{message}\n\n[Resolved User Message]\n{routing_message}"
            if context
            else f"[Original User Message]\n{message}\n\n[Resolved User Message]\n{routing_message}"
        )
        deps.trace.record_current_event(
            stage="intent",
            status="info",
            title="Intent heuristic pass-through",
            detail={
                "reason": "no_heuristic_match_before_llm",
                "signals": signals,
            },
        )

        try:
            raw = await deps.router.generate(
                system_prompt=_INTENT_SYSTEM_PROMPT,
                user_content=user_content,
                response_schema=IntentOutput,
            )
            output = IntentOutput.model_validate_json(raw)
        except Exception as exc:
            logger.warning("Intent analysis failed, using fallback: %s", exc)
            _record_intent_fallback(
                deps,
                reason="intent_llm_exception",
                signals={**signals, "error_type": type(exc).__name__},
            )
            return _build_result(INTENT_FALLBACK, state)

        profile_changes_dict = None
        if output.profile_changes:
            profile_changes_dict = {item.field: item.value for item in output.profile_changes}
        output_intent = _coerce_llm_intent(output.intent, state, routing_message)
        contract = _contract_fields(
            output_intent,
            state,
            record_type=output.record_type,
            modify_target=output.modify_target,
            profile_changes=profile_changes_dict,
            routing_message=routing_message,
            emotion_override={
                "label": output.emotion.label,
                "intensity": output.emotion.intensity,
            },
        )
        routing_diagnostics = _routing_diagnostics(signals, output_intent, contract)
        contract["ambiguous"] = bool(contract.get("ambiguous") or routing_diagnostics.get("domain_ambiguous"))
        deps.trace.record_current_event(
            stage="intent",
            status="warn" if output_intent == INTENT_FALLBACK else "ok",
            title="LLM intent decision",
            detail={
                "raw_intent": output.intent,
                "coerced_intent": output_intent,
                "confidence": output.confidence,
                "signals": signals,
                "routing_diagnostics": routing_diagnostics,
                "fallback_reason": "llm_or_coercion_returned_fallback"
                if output_intent == INTENT_FALLBACK
                else None,
            },
        )

        return {
            "intent": output_intent,
            **contract,
            "routing_diagnostics": routing_diagnostics,
            "confidence": output.confidence,
            "emotion": {
                "label": output.emotion.label,
                "intensity": output.emotion.intensity,
            },
            "previous_intent": state.get("intent"),
            "previous_emotion": state.get("emotion"),
            "requires_past_memory": output.requires_past_memory,
            "should_save_episode": output.should_save_episode,
            "short_term_memory_query": False,
            "has_fact_change": output.has_fact_change,
            "record_type": output.record_type,
            "profile_changes": profile_changes_dict,
            "is_today": output.is_today,
            "modify_target": output.modify_target,
            "search_targets": output.search_targets,
            "search_retry_count": 0,
            "fallback_count": state.get("fallback_count", 0),
            "self_eval_count": 0,
        }

    return analyze_intent_node


def _build_result(
    intent: str,
    state: GraphState,
    *,
    confidence: float = 1.0,
    search_targets: list[str] | None = None,
    requires_past_memory: bool = False,
    should_save_episode: bool = False,
    short_term_memory_query: bool = False,
    modify_target: str | None = None,
    record_type: str | None = None,
    is_today: bool | None = None,
    domain_override: str | None = None,
) -> dict:
    is_profile_record = intent == INTENT_RECORD and _looks_like_profile_record(str(state.get("user_message") or ""))
    resolved_record_type = record_type or ("profile" if is_profile_record else None)
    contract = _contract_fields(
        intent,
        state,
        record_type=resolved_record_type,
        modify_target=modify_target,
        domain_override=domain_override,
    )
    routing_diagnostics = _routing_diagnostics({}, intent, contract)
    return {
        "intent": intent,
        **contract,
        "routing_diagnostics": routing_diagnostics,
        "confidence": confidence,
        "emotion": state.get("emotion") or {"label": "중립", "intensity": 0.0},
        "previous_intent": state.get("intent"),
        "previous_emotion": state.get("emotion"),
        "requires_past_memory": requires_past_memory,
        "should_save_episode": should_save_episode,
        "short_term_memory_query": short_term_memory_query,
        "has_fact_change": False,
        "record_type": resolved_record_type,
        "profile_changes": None,
        "is_today": is_today,
        "modify_target": modify_target,
        "search_targets": search_targets or [],
        "search_retry_count": 0,
        "fallback_count": state.get("fallback_count", 0),
        "self_eval_count": 0,
    }


def _ambiguous_plan_clarification_result(state: GraphState, signals: dict[str, Any]) -> dict:
    result = _build_result(INTENT_PLAN, state, confidence=0.9)
    result["ambiguous"] = True
    result["needs_clarification"] = True
    result["routing_diagnostics"] = {
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "general",
        "inferred_domain": signals.get("inferred_domain"),
        "domain_ambiguous": True,
        "needs_clarification_recommended": True,
        "reason_codes": ["ambiguous_mixed_plan_domain"],
        "plan_like": True,
        "info_like": bool(signals.get("info_request_match") or signals.get("question_followup_match")),
        "context_ambiguous": bool(signals.get("context_ambiguous")),
    }
    return result


def _record_intent_fallback(deps: NodeDeps, *, reason: str, signals: dict[str, Any]) -> None:
    deps.trace.record_current_event(
        stage="intent",
        status="warn",
        title="Intent fallback selected",
        detail={
            "reason": reason,
            "signals": signals,
        },
    )


def _intent_signal_snapshot(message: str, routing_message: str, state: GraphState) -> dict[str, Any]:
    normalized = routing_message.strip().lower()
    resolution = state.get("context_resolution") or {}
    return {
        "message_length": len(message),
        "routing_message_length": len(routing_message),
        "inferred_domain": infer_domain(routing_message),
        "resolved_reference": resolution.get("resolved_reference"),
        "resolved_domain": resolution.get("resolved_domain"),
        "context_ambiguous": bool(resolution.get("ambiguous")),
        "context_confidence": resolution.get("confidence"),
        "safety_match": _looks_like_safety_request(message),
        "care_match": _looks_like_care_request(routing_message),
        "health_context_match": _looks_like_health_context(routing_message),
        "offtopic_match": _looks_like_offtopic_request(routing_message),
        "question_followup_match": _looks_like_question_followup(routing_message),
        "plan_request_match": _looks_like_plan_request(routing_message),
        "plan_delete_match": _looks_like_plan_delete_request(routing_message),
        "info_request_match": _looks_like_info_request(routing_message),
        "modify_request_match": _looks_like_modify_request(routing_message),
        "profile_record_match": _looks_like_profile_record(routing_message),
        "memory_query_match": _looks_like_memory_query(routing_message),
        "has_question_mark": "?" in normalized,
    }


def _routing_diagnostics(signals: dict[str, Any], intent: str, contract: dict[str, Any]) -> dict[str, Any]:
    plan_like = bool(signals.get("plan_request_match") or signals.get("modify_request_match"))
    info_like = bool(signals.get("info_request_match") or signals.get("question_followup_match"))
    inferred_domain = signals.get("inferred_domain")
    action_intent = contract.get("action_intent")
    domain = contract.get("domain")
    domain_ambiguous = bool(
        contract.get("ambiguous")
        or (plan_like and info_like and action_intent in {"create", "modify", "info"})
        or (action_intent in {"create", "modify"} and domain == "general")
        or (inferred_domain == "general" and action_intent in {"create", "modify"})
    )
    reason_codes: list[str] = []
    if contract.get("ambiguous"):
        reason_codes.append("llm_contract_ambiguous")
    if plan_like and info_like:
        reason_codes.append("plan_info_overlap")
    if action_intent in {"create", "modify"} and domain == "general":
        reason_codes.append("plan_domain_general")
    if inferred_domain == "general" and action_intent in {"create", "modify"}:
        reason_codes.append("inferred_domain_general")
    if signals.get("context_ambiguous"):
        reason_codes.append("context_ambiguous")
    return {
        "intent": intent,
        "action_intent": action_intent,
        "domain": domain,
        "inferred_domain": inferred_domain,
        "domain_ambiguous": domain_ambiguous,
        "needs_clarification_recommended": domain_ambiguous and action_intent in {"create", "modify"},
        "reason_codes": reason_codes,
        "plan_like": plan_like,
        "info_like": info_like,
        "context_ambiguous": bool(signals.get("context_ambiguous")),
    }


def _contract_fields(
    intent: str,
    state: GraphState,
    *,
    record_type: str | None = None,
    modify_target: str | None = None,
    profile_changes: dict | None = None,
    routing_message: str | None = None,
    emotion_override: dict | None = None,
    domain_override: str | None = None,
) -> dict:
    action_intent = _action_intent_from_legacy(intent)
    support_mode = _support_mode(intent, state, routing_message, emotion_override)
    resolution = state.get("context_resolution") or {}
    resolved_domain = resolution.get("resolved_domain")
    active_proposal = state.get("active_proposal") or {}
    resolved_reference = resolution.get("resolved_reference")
    effective_message = routing_message or state.get("user_message")
    inferred_domain = infer_domain(effective_message)

    domain = "general"
    if domain_override in {"workout", "diet", "profile", "general"}:
        domain = domain_override
    elif action_intent == "safety":
        domain = "general"
    elif record_type == "profile" or profile_changes:
        domain = "profile"
    elif modify_target in {"workout", "diet"}:
        domain = modify_target
    elif resolved_domain in {"workout", "diet", "profile"}:
        domain = resolved_domain
    elif action_intent in {"create", "modify", "approval"} and inferred_domain in {"workout", "diet"}:
        domain = inferred_domain
    elif action_intent == "approval" and state.get("proposed_plan_type") in {"workout", "diet"}:
        domain = str(state.get("proposed_plan_type"))
    elif resolved_reference == "active_proposal" and active_proposal.get("domain") in {"workout", "diet"}:
        domain = str(active_proposal["domain"])
    elif inferred_domain in {"workout", "diet"}:
        domain = inferred_domain
    elif inferred_domain == "profile" and action_intent != "casual":
        domain = inferred_domain
    else:
        domain = "general"

    ambiguous = bool(resolution.get("ambiguous")) or intent == INTENT_FALLBACK
    return {
        "action_intent": action_intent,
        "domain": domain,
        "support_mode": support_mode,
        "ambiguous": ambiguous,
    }


def _action_intent_from_legacy(intent: str) -> str:
    if intent == INTENT_PLAN:
        return "create"
    if intent == INTENT_MODIFY:
        return "modify"
    if intent == INTENT_INFO:
        return "info"
    if intent == INTENT_RECORD:
        return "record"
    if intent == INTENT_APPROVAL:
        return "approval"
    if intent == INTENT_CASUAL:
        return "casual"
    if intent == INTENT_SAFETY:
        return "safety"
    if intent == INTENT_HOME_RECOMMENDATION:
        return "home_recommendation"
    if intent == INTENT_CARE:
        return "care"
    return "fallback"


def _support_mode(
    intent: str,
    state: GraphState,
    routing_message: str | None,
    emotion_override: dict | None = None,
) -> str:
    if intent == INTENT_CARE:
        return "care"

    normalized = str(routing_message or state.get("user_message") or "").lower()
    if intent in {INTENT_PLAN, INTENT_MODIFY} and _looks_like_plan_request(normalized):
        strong_care_markers = (
            "지쳐",
            "지쳤",
            "힘들",
            "불안",
            "우울",
            "무기력",
            "스트레스",
            "버겁",
            "외롭",
            "실패",
            "못 하겠",
            "못하겠",
            "하기 싫",
            "하기싫",
            "폭식",
            "망쳐",
            "망했",
        )
        if not any(marker in normalized for marker in strong_care_markers):
            return "normal"

    if any(marker in normalized for marker in _CARE_SUPPORT_MARKERS):
        return "care"

    emotion = emotion_override or state.get("emotion") or {}
    emotion_intensity = _safe_float(emotion.get("intensity"))
    if emotion_intensity >= 0.6:
        return "care"

    emotion_label = str(emotion.get("label") or "").lower()
    if any(marker in emotion_label for marker in ("불안", "우울", "슬픔", "외로움", "stress", "anx", "sad")):
        return "care"

    return "normal"


def _coerce_llm_intent(intent: str, state: GraphState, routing_message: str) -> str:
    intent = normalize_intent(intent)
    if intent == INTENT_APPROVAL and not _has_pending_plan_confirmation_v2(state):
        if _looks_like_modify_request(routing_message):
            return INTENT_MODIFY
        if _looks_like_plan_request(routing_message):
            return INTENT_PLAN
        if _looks_like_profile_record(routing_message):
            return INTENT_RECORD
        return INTENT_FALLBACK
    return intent


def _routing_message(state: GraphState, message: str) -> str:
    resolution = state.get("context_resolution") or {}
    resolved_reference = resolution.get("resolved_reference")
    resolved_text = str(resolution.get("resolved_text") or "").strip()
    confidence = _safe_float(resolution.get("confidence"))
    if resolved_reference and resolved_reference != "none" and resolved_text and confidence >= 0.6:
        return resolved_text
    return message


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_assistant_message(state: GraphState) -> str:
    recent_turns = (state.get("recent_dialogue") or {}).get("recent_turns") or []
    for turn in reversed(recent_turns):
        assistant_text = str(turn.get("assistant_text") or "").strip()
        if assistant_text:
            return assistant_text
    return ""


def _latest_assistant_message_v2(state: GraphState) -> str:
    return _latest_assistant_message(state)


def _build_context_v3(state: GraphState) -> str:
    parts: list[str] = []

    if state.get("previous_intent"):
        parts.append(f"이전 의도: {state['previous_intent']}")

    if state.get("previous_emotion"):
        emotion = state["previous_emotion"]
        parts.append(f"이전 감정: {emotion['label']} (강도 {emotion['intensity']:.1f})")

    latest_assistant = _latest_assistant_message_v2(state)
    if latest_assistant:
        parts.append(f"직전 AI 응답: {latest_assistant[:200]}")

    resolution = state.get("context_resolution") or {}
    if resolution.get("resolved_reference") != "none" and resolution.get("resolved_text"):
        parts.append(
            "Resolved context: "
            f"{resolution.get('resolved_reference')} / {resolution.get('resolved_domain')} / "
            f"{str(resolution.get('resolved_text') or '')[:200]}"
        )

    recent_turns = ((state.get("recent_dialogue") or {}).get("recent_turns") or [])[-2:]
    if recent_turns:
        parts.append(
            "Recent dialogue summary:\n"
            + "\n".join(
                f"- {turn.get('action_intent')}/{turn.get('domain')}: {turn.get('user_summary')}"
                for turn in recent_turns
            )
        )

    return "\n".join(parts)


def _has_pending_plan_confirmation_v2(state: GraphState) -> bool:
    if bool(state.get("awaiting_plan_confirmation")) and bool(state.get("proposed_plan")):
        return True
    active_proposal = state.get("active_proposal")
    return bool(active_proposal and active_proposal.get("items"))


def _looks_like_safety_request(message: str) -> bool:
    return bool(_SAFETY_PATTERNS.search(message) or _PHYSICAL_EMERGENCY_PATTERNS.search(message))


def _pending_plan_domain(state: GraphState, message: str | None = None) -> str | None:
    inferred = infer_domain(message or state.get("user_message"))
    if inferred in {"workout", "diet"}:
        return inferred

    active_proposal = state.get("active_proposal") or {}
    active_domain = active_proposal.get("domain")
    if active_domain in {"workout", "diet"}:
        return str(active_domain)
    proposed_plan_type = state.get("proposed_plan_type")
    if proposed_plan_type in {"workout", "diet"}:
        return str(proposed_plan_type)
    return None


def _looks_like_pending_plan_revision(message: str, routing_message: str, state: GraphState) -> bool:
    if not _has_pending_plan_confirmation_v2(state):
        return False

    combined_normalized = " ".join(
        candidate.strip().lower() for candidate in (message, routing_message) if candidate.strip()
    )
    if _looks_like_ambiguous_mixed_plan_request(combined_normalized):
        return False
    if _looks_like_plan_scope_correction(message, routing_message, state):
        return True
    if _looks_like_read_only_info_request(message, routing_message) or _looks_like_new_plan_request(message, routing_message):
        return False
    if _looks_like_modified_plan_acceptance(combined_normalized):
        return False

    candidates = [message, routing_message]
    for candidate in candidates:
        if _looks_like_explicit_plan_change(candidate, state) or _looks_like_modify_request(candidate):
            return True

    if not combined_normalized:
        return False

    has_reference = any(
        keyword in combined_normalized
        for keyword in (*_PLAN_REFERENCE_KEYWORDS, *_PLAN_CONFIRMATION_REFERENCE_KEYWORDS)
    )
    has_revision_language = any(
        marker in combined_normalized
        for marker in (
            "더 안전",
            "안전하게",
            "현실적으로",
            "짧게",
            "가볍게",
            "10분",
            "15분",
            "부담",
            "무리",
            "제외",
            "대체",
            "빼고",
        )
    )
    return has_reference and has_revision_language


def _looks_like_plan_scope_correction(message: str, routing_message: str, state: GraphState) -> bool:
    if not _has_pending_plan_confirmation_v2(state):
        return False

    combined = " ".join(candidate.strip().lower() for candidate in (message, routing_message) if candidate.strip())
    if not combined:
        return False

    compact = re.sub(r"\s+", "", combined)
    has_plan_reference = any(keyword in combined for keyword in _PLAN_DOMAIN_KEYWORDS) or any(
        keyword in combined for keyword in (*_PLAN_REFERENCE_KEYWORDS, *_PLAN_CONFIRMATION_REFERENCE_KEYWORDS)
    )
    if not has_plan_reference and _pending_plan_domain(state, combined) not in {"workout", "diet"}:
        return False

    requested_range = any(
        marker in compact
        for marker in (
            "일주일",
            "한주",
            "일주",
            "1주",
            "7일",
            "주간",
            "한달",
            "1달",
            "1개월",
            "월간",
            "30일",
        )
    ) or bool(re.search(r"\d+\s*(?:주|일|달|개월)", combined))
    if not requested_range:
        return False

    scope_complaint = any(
        marker in compact
        for marker in (
            "라니까",
            "아니",
            "하루만",
            "오늘만",
            "일만",
            "날짜",
            "누락",
            "부족",
            "빠졌",
            "안나왔",
            "한번만",
        )
    )
    date_only_complaint = bool(
        re.search(r"\d{4}-\d{2}-\d{2}.*만", combined)
        or re.search(r"\d{1,2}\s*월\s*\d{1,2}\s*일\s*만", combined)
    )
    why_only_complaint = "왜" in compact and any(marker in compact for marker in ("만", "하루", "오늘", "날짜"))
    return scope_complaint or date_only_complaint or why_only_complaint


def _looks_like_ambiguous_mixed_plan_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    has_plan_request = _looks_like_plan_request(normalized)
    has_workout = any(keyword in normalized for keyword in ("운동", "러닝", "헬스", "근력", "유산소", "스트레칭", "산책", "workout", "exercise"))
    has_diet = any(keyword in normalized for keyword in ("식단", "식사", "메뉴", "아침", "점심", "저녁", "meal", "diet"))
    return has_plan_request and has_workout and has_diet


def _matches_hardcoded_confirmation_approval(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", message.strip().lower())
    if not normalized:
        return False

    if _looks_like_pending_plan_revision(message, message, {"awaiting_plan_confirmation": True, "proposed_plan": [{}]}):
        return False

    if normalized in {phrase.lower() for phrase in _EXPLICIT_PLAN_APPROVAL_PHRASES}:
        return True

    has_plan_reference = any(keyword in normalized for keyword in _PLAN_REFERENCE_KEYWORDS)
    has_apply_verb = any(keyword in normalized for keyword in _APPROVAL_COMMITMENT_KEYWORDS)
    return has_plan_reference and has_apply_verb and not _looks_like_explicit_plan_change(message, {})


async def _classify_plan_confirmation(
    deps: NodeDeps,
    state: GraphState,
    message: str,
) -> PlanConfirmationDecision | None:
    latest_assistant = _latest_assistant_message_v2(state)
    proposed_plan = state.get("proposed_plan") or []
    proposed_plan_type = state.get("proposed_plan_type") or (state.get("active_proposal") or {}).get("domain") or ""
    proposed_plan_action = state.get("proposed_plan_action") or (state.get("active_proposal") or {}).get("write_mode") or ""
    user_content = (
        "[Assistant Plan Response]\n"
        f"{latest_assistant[:800]}\n\n"
        "[Proposed Plan Meta]\n"
        f"plan_type={proposed_plan_type}\n"
        f"plan_action={proposed_plan_action}\n"
        f"item_count={len(proposed_plan)}\n\n"
        "[User Message]\n"
        f"{message}"
    )

    try:
        raw = await deps.router.generate(
            system_prompt=_PLAN_CONFIRMATION_SYSTEM_PROMPT,
            user_content=user_content,
            response_schema=PlanConfirmationDecision,
        )
        decision = PlanConfirmationDecision.model_validate_json(raw)
        deps.trace.record_current_event(
            stage="confirm_gate",
            status="ok" if decision.approved else "warn",
            title="LLM confirmation decision",
            detail={
                "approved": decision.approved,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "user_message": message,
                "last_assistant_excerpt": latest_assistant[:200],
                "proposed_plan_count": len(proposed_plan),
                "proposed_plan_type": proposed_plan_type,
                "proposed_plan_action": proposed_plan_action,
            },
        )
        return decision
    except Exception as exc:
        logger.warning("Plan confirmation classification failed, falling back: %s", exc)
        deps.trace.record_current_alert(
            severity="warning",
            message="Plan confirmation classification failed",
            detail={"error": str(exc), "user_message": message},
        )
        if _looks_like_plan_approval(message, state) or _looks_like_plan_acceptance_followup(message, state):
            deps.trace.record_current_event(
                stage="confirm_gate",
                status="ok",
                title="Heuristic confirmation fallback approved",
                detail={"reason": "heuristic_fallback", "user_message": message},
            )
            return PlanConfirmationDecision(approved=True, confidence=0.9, reason="heuristic_fallback")
        return None


def _has_recent_plan_intent(state: GraphState) -> bool:
    return state.get("intent") in {INTENT_PLAN, INTENT_MODIFY, INTENT_APPROVAL} or state.get("previous_intent") in {
        INTENT_PLAN,
        INTENT_MODIFY,
        INTENT_APPROVAL,
    }


def _looks_like_explicit_plan_change(message: str, state: GraphState | dict) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    if _looks_like_read_only_info_request(normalized) or _looks_like_new_plan_request(normalized):
        return False

    has_change_marker = any(marker in normalized for marker in _PLAN_CHANGE_MARKERS)
    has_modify_keyword = any(keyword in normalized for keyword in _MODIFY_KEYWORDS)
    has_commitment_keyword = any(keyword in normalized for keyword in _APPROVAL_COMMITMENT_KEYWORDS)
    has_confirmation_reference = any(keyword in normalized for keyword in _PLAN_CONFIRMATION_REFERENCE_KEYWORDS)
    looks_like_referential_acceptance = has_confirmation_reference and has_commitment_keyword and not (
        has_change_marker or has_modify_keyword
    )
    if _looks_like_modified_plan_acceptance(normalized):
        return False

    if has_modify_keyword and not looks_like_referential_acceptance:
        return True
    if has_change_marker and not looks_like_referential_acceptance:
        return True
    return False


def _looks_like_modified_plan_acceptance(normalized: str) -> bool:
    has_commitment_keyword = any(keyword in normalized for keyword in _APPROVAL_COMMITMENT_KEYWORDS)
    return (
        has_commitment_keyword
        and any(marker in normalized for marker in ("수정한 계획", "수정된 계획", "수정안", "방금 수정"))
        and not any(marker in normalized for marker in ("말고", "대신", "다시 바꿔", "다시 수정", "변경해"))
    )


def _looks_like_plan_acceptance_followup(message: str, state: GraphState) -> bool:
    normalized = message.strip().lower()
    if not normalized or not _has_pending_plan_confirmation_v2(state):
        return False
    if _looks_like_explicit_plan_change(message, state):
        return False
    if _looks_like_profile_record(message):
        return False
    if _looks_like_safety_request(message):
        return False

    is_short_ack = normalized in {item.lower() for item in _SHORT_APPROVAL_RESPONSES}
    has_commitment_keyword = any(keyword in normalized for keyword in _APPROVAL_COMMITMENT_KEYWORDS)
    has_reference = any(
        keyword in normalized
        for keyword in (*_PLAN_REFERENCE_KEYWORDS, *_PLAN_CONFIRMATION_REFERENCE_KEYWORDS)
    )

    if is_short_ack:
        return True
    return has_commitment_keyword and has_reference


def _assistant_requested_plan_confirmation(state: GraphState) -> bool:
    latest_assistant = _latest_assistant_message_v2(state).lower()
    if not latest_assistant:
        return False

    has_plan_reference = any(keyword in latest_assistant for keyword in _PLAN_REFERENCE_KEYWORDS)
    has_confirmation_prompt = any(keyword in latest_assistant for keyword in _APPROVAL_KEYWORDS)
    return has_plan_reference and has_confirmation_prompt


def _looks_like_plan_request(message: str) -> bool:
    normalized = message.strip().lower()
    has_domain_keyword = any(keyword in normalized for keyword in _PLAN_DOMAIN_KEYWORDS)
    has_request_keyword = any(keyword in normalized for keyword in _PLAN_REQUEST_KEYWORDS)
    has_excluded_keyword = any(keyword in normalized for keyword in _PLAN_EXCLUDE_KEYWORDS)
    return has_domain_keyword and has_request_keyword and not has_excluded_keyword


def _looks_like_new_plan_request(message: str, routing_message: str | None = None) -> bool:
    candidates = [message, routing_message or ""]
    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        has_new_marker = any(marker in normalized for marker in _NEW_PLAN_MARKERS)
        if has_new_marker and _looks_like_plan_request(normalized):
            return True
    return False


def _looks_like_care_request(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in _CARE_SUPPORT_MARKERS)


def _looks_like_emotional_care_request(message: str, routing_message: str | None = None) -> bool:
    combined = " ".join(candidate.strip().lower() for candidate in (message, routing_message or "") if candidate.strip())
    if not combined:
        return False
    if _looks_like_plan_request(combined) or _looks_like_modify_request(combined) or _looks_like_plan_check_record(combined):
        return False
    if not _looks_like_care_request(combined):
        return False
    if any(marker in combined for marker in ("궁금", "되는지", "해도 될", "해도 되는")) and not any(
        marker in combined for marker in ("망가", "말해줘", "자신감", "할 수", "해낼", "위로", "응원")
    ):
        return False
    reassurance_markers = (
        "말해줘",
        "망가지는",
        "망가지",
        "할 수",
        "자신감",
        "루틴",
        "쉬어도",
        "쉬어야",
        "해낼",
        "위로",
        "응원",
    )
    return any(marker in combined for marker in reassurance_markers)


def _looks_like_simple_condition_statement(message: str, routing_message: str | None = None) -> bool:
    combined = " ".join(candidate.strip().lower() for candidate in (message, routing_message or "") if candidate.strip())
    if not combined:
        return False
    if not _looks_like_care_request(combined):
        return False
    if "?" in combined or "？" in combined:
        return False
    if _looks_like_question_followup(combined) or _looks_like_read_only_info_request(combined):
        return False
    emotional_markers = (
        "외로",
        "불안",
        "우울",
        "무기력",
        "멘탈",
        "스트레스",
        "자신감",
        "망했",
        "실패",
        "lonely",
        "anxious",
        "depressed",
        "stress",
    )
    if any(marker in combined for marker in emotional_markers):
        return False
    physical_markers = (
        "잠",
        "수면",
        "식욕",
        "허리",
        "뻐근",
        "피곤",
        "컨디션",
        "피곤",
        "지침",
        "컨디션",
        "졸려",
        "배고",
        "근육통",
        "몸살",
        "아파",
        "피로",
        "tired",
        "fatigue",
        "sore",
        "hungry",
    )
    if not any(marker in combined for marker in physical_markers):
        return False
    action_markers = (
        "쉬어도",
        "해야",
        "될까",
        "괜찮",
        "어떻게",
        "말해줘",
        "위로",
        "응원",
        "도와",
        "추천",
        "계획",
        "플랜",
        "짜줘",
        "작성",
        "해줘",
        "어떻게",
        "쉬어도",
        "해야",
        "될까",
        "괜찮",
        "tell me",
        "comfort",
        "encourage",
        "help",
        "recommend",
        "plan",
        "should",
        "can i",
    )
    return not any(marker in combined for marker in action_markers)


def _looks_like_condition_info_question(message: str, routing_message: str | None = None) -> bool:
    combined = " ".join(candidate.strip().lower() for candidate in (message, routing_message or "") if candidate.strip())
    if not combined:
        return False
    if _looks_like_plan_request(combined) or _looks_like_modify_request(combined):
        return False
    rest_question_markers = ("쉬어도", "쉬어도 돼", "쉬어도 될", "rest today", "take a rest")
    if any(marker in combined for marker in rest_question_markers):
        return True
    condition_markers = (
        "피곤",
        "잠",
        "수면",
        "졸려",
        "식욕",
        "배고",
        "허리",
        "뻐근",
        "근육통",
        "컨디션",
        "피로",
        "tired",
        "sleep",
        "appetite",
        "hungry",
        "stiff",
        "sore",
    )
    if not any(marker in combined for marker in condition_markers):
        return False
    question_markers = (
        "?",
        "？",
        "쉬어도",
        "해야",
        "될까",
        "괜찮",
        "어떻게",
        "좋을까",
        "먹어도",
        "해도",
        "should",
        "can i",
        "is it ok",
    )
    return any(marker in combined for marker in question_markers)


def _looks_like_mixed_plan_clarification_followup(message: str, state: GraphState) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    if not any(marker in normalized for marker in ("둘 다", "둘다", "같이", "함께", "both", "순서", "전부", "다 해")):
        return False
    latest_assistant = _latest_assistant_message_v2(state).lower()
    return bool(
        latest_assistant
        and any(marker in latest_assistant for marker in ("운동", "workout"))
        and any(marker in latest_assistant for marker in ("식단", "diet"))
        and any(marker in latest_assistant for marker in ("골라", "먼저", "하나", "선택"))
    )


def _looks_like_pending_sequential_plan_followup(message: str, state: GraphState) -> bool:
    pending = state.get("pending_sequential_plan") or {}
    pending_domain = str(pending.get("domain") or "")
    if pending_domain not in {"workout", "diet"}:
        return False
    if state.get("awaiting_plan_confirmation") or _has_pending_plan_confirmation_v2(state):
        return False
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    domain_markers = {
        "diet": ("식단", "식사", "메뉴", "영양", "밥", "meal", "diet", "menu", "nutrition"),
        "workout": ("운동", "루틴", "근력", "유산소", "스트레칭", "workout", "exercise", "routine"),
    }
    continuation_markers = ("이어서", "다음", "계속", "마저", "나머지", "그 다음", "까지", "도", "continue", "next", "too", "also")
    request_markers = ("해줘", "짜줘", "작성", "만들", "부탁", "진행", "plan", "make", "create")
    has_domain_marker = any(marker in normalized for marker in domain_markers[pending_domain])
    has_continuation = any(marker in normalized for marker in continuation_markers)
    has_request = any(marker in normalized for marker in request_markers)
    other_domain = "workout" if pending_domain == "diet" else "diet"
    has_other_domain_marker = any(marker in normalized for marker in domain_markers[other_domain])
    short_domain_followup = has_domain_marker and len(normalized.replace(" ", "")) <= 12
    if has_other_domain_marker and not has_domain_marker:
        return False
    return (
        (has_domain_marker and (has_request or has_continuation))
        or (has_continuation and has_request)
        or short_domain_followup
    )


def _looks_like_health_context(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in _HEALTH_CONTEXT_KEYWORDS)


def _looks_like_offtopic_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    if _looks_like_safety_request(normalized):
        return False
    if (
        any(keyword in normalized for keyword in _PLAN_DOMAIN_KEYWORDS)
        or any(keyword in normalized for keyword in _PROFILE_FIELD_KEYWORDS)
        or _looks_like_health_context(normalized)
        or _looks_like_care_request(normalized)
    ):
        return False
    return bool(_OFFTOPIC_PATTERNS.search(normalized))


def _looks_like_context_setup(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in ("내 상황 기억", "내 조건 기억", "내 상황 고려", "내 조건 고려", "기억하고 답"))


def _looks_like_question_followup(message: str) -> bool:
    normalized = message.strip().lower()
    if any(
        marker in normalized
        for marker in (
            "왜",
            "이유",
            "근거",
            "설명",
            "어떻게",
            "피해야",
            "해도 돼",
            "해도 될",
            "괜찮",
            "쉬어야",
            "쉬어도",
            "쉴까",
            "될까",
            "좋을까",
            "하면 좋",
            "뭐부터",
            "먹지",
            "먹어도",
            "마셔야",
        )
    ):
        return True
    return normalized.endswith("?")


def _looks_like_read_only_info_request(message: str, routing_message: str | None = None) -> bool:
    combined = " ".join(candidate.strip().lower() for candidate in (message, routing_message or "") if candidate.strip())
    if not combined:
        return False

    readonly_markers = (
        "이유",
        "근거",
        "설명",
        "정리",
        "판단",
        "알려줘",
        "피해야",
        "괜찮",
        "될까",
        "되는지",
        "궁금",
        "쉬어도",
        "빠른지",
        "적절한지",
        "맞는지",
    )
    if not any(marker in combined for marker in readonly_markers):
        return False
    if any(marker in combined for marker in ("저장", "반영", "적용", "진행", "확정", "체크해", "완료")):
        return False
    if _looks_like_new_plan_request(message, routing_message):
        return False
    return _looks_like_info_request(combined) or _looks_like_question_followup(combined)


def _looks_like_info_request(message: str) -> bool:
    normalized = message.strip().lower()
    has_domain_keyword = any(keyword in normalized for keyword in _PLAN_DOMAIN_KEYWORDS) or any(
        keyword in normalized for keyword in _PROFILE_FIELD_KEYWORDS
    )
    if "내 조건" in normalized or "내 상황" in normalized:
        has_domain_keyword = True
    if _looks_like_health_context(normalized):
        has_domain_keyword = True
    has_info_marker = any(marker in normalized for marker in _INFO_REQUEST_MARKERS)
    return has_domain_keyword and has_info_marker


def _looks_like_modify_request(message: str) -> bool:
    normalized = message.strip().lower()
    has_domain_keyword = any(keyword in normalized for keyword in _PLAN_DOMAIN_KEYWORDS)
    has_modify_keyword = any(keyword in normalized for keyword in _MODIFY_KEYWORDS)
    has_hard_modify_keyword = any(
        keyword in normalized
        for keyword in (
            "수정",
            "바꿔",
            "변경",
            "교체",
            "조정",
            "빼",
            "다시",
            "줄여",
            "늘려",
            "추가",
            "제외",
            "대체",
            "제거",
        )
    )
    if _looks_like_plan_request(normalized) and not has_hard_modify_keyword:
        return False
    return has_domain_keyword and has_modify_keyword


def _looks_like_plan_delete_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False

    has_plan_context = any(
        keyword in normalized
        for keyword in (
            "플랜",
            "계획",
            "일정",
            "루틴",
            "운동",
            "식단",
            "식사",
            "메뉴",
            "workout",
            "exercise",
            "diet",
            "meal",
        )
    )
    has_delete_marker = any(
        marker in normalized
        for marker in (
            "삭제",
            "지워",
            "지우",
            "없애",
            "취소",
            "remove",
            "delete",
            "cancel",
        )
    )
    return has_plan_context and has_delete_marker


def _infer_plan_delete_target(message: str) -> str | None:
    normalized = message.strip().lower()
    has_workout = any(
        keyword in normalized
        for keyword in ("운동", "루틴", "헬스", "근력", "유산소", "workout", "exercise")
    )
    has_diet = any(
        keyword in normalized
        for keyword in ("식단", "식사", "메뉴", "아침", "점심", "저녁", "diet", "meal")
    )
    if has_workout and not has_diet:
        return "workout"
    if has_diet and not has_workout:
        return "diet"
    return None


def _looks_like_plan_check_record(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized or _looks_like_profile_record(normalized):
        return False
    has_plan_context = any(
        marker in normalized
        for marker in (
            "운동",
            "식단",
            "계획",
            "플랜",
            "루틴",
            "첫 번째",
            "첫번째",
            "방금 저장한",
            "저장한 계획",
        )
    )
    has_record_marker = any(
        marker in normalized
        for marker in (
            "완료",
            "완료했",
            "했어",
            "했어요",
            "체크",
            "체크해",
            "처리해",
            "처리",
        )
    )
    return has_plan_context and has_record_marker


def _looks_like_plan_approval(message: str, state: GraphState) -> bool:
    normalized = message.strip().lower()
    has_approval_keyword = any(keyword in normalized for keyword in _APPROVAL_KEYWORDS)
    has_commitment_keyword = any(keyword in normalized for keyword in _APPROVAL_COMMITMENT_KEYWORDS)
    has_explicit_approval_phrase = any(phrase in normalized for phrase in _EXPLICIT_PLAN_APPROVAL_PHRASES)
    has_plan_reference = any(keyword in normalized for keyword in _PLAN_REFERENCE_KEYWORDS)
    has_confirmation_reference = any(keyword in normalized for keyword in _PLAN_CONFIRMATION_REFERENCE_KEYWORDS)
    has_modify_keyword = any(keyword in normalized for keyword in _MODIFY_KEYWORDS)
    has_profile_keyword = any(keyword in normalized for keyword in _PROFILE_FIELD_KEYWORDS)
    has_plan_context = (
        _has_pending_plan_confirmation_v2(state)
        or bool(state.get("proposed_plan"))
        or _has_recent_plan_intent(state)
        or _assistant_requested_plan_confirmation(state)
    )

    if _looks_like_explicit_plan_change(message, state) or _looks_like_pending_plan_revision(message, message, state):
        return False
    if has_plan_context and has_explicit_approval_phrase and not has_profile_keyword:
        return True
    if has_plan_context and has_confirmation_reference and has_commitment_keyword and not has_profile_keyword:
        return True
    if not has_approval_keyword:
        return False
    if not (has_plan_reference or has_plan_context):
        return False
    if has_profile_keyword:
        return False
    if has_modify_keyword and not (has_plan_context and has_commitment_keyword):
        return False
    return True


def _looks_like_profile_record(message: str) -> bool:
    normalized = message.strip().lower()
    has_profile_field = any(keyword in normalized for keyword in _PROFILE_FIELD_KEYWORDS)
    has_update_keyword = any(keyword in normalized for keyword in _PROFILE_UPDATE_KEYWORDS)
    has_plan_domain = any(keyword in normalized for keyword in ("운동 계획", "식단 계획", "운동 루틴", "식단 루틴"))
    return has_profile_field and has_update_keyword and not has_plan_domain


def _looks_like_memory_save_request(message: str) -> bool:
    normalized = message.strip().lower()
    return any(keyword in normalized for keyword in _MEMORY_SAVE_KEYWORDS)


def _looks_like_memory_query(message: str) -> bool:
    normalized = message.strip().lower()
    has_memory_keyword = any(keyword in normalized for keyword in _MEMORY_QUERY_KEYWORDS)
    has_question_shape = (
        "?" in normalized
        or normalized.endswith("뭐야")
        or normalized.endswith("뭐지")
        or normalized.endswith("기억나")
    )
    return has_memory_keyword and has_question_shape


def _looks_like_short_term_memory_query(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        marker in normalized
        for marker in (
            "방금",
            "아까",
            "조금 전",
            "좀 전에",
            "이번 대화",
            "내 별명",
        )
    )


def _looks_like_context_dependent_fallback(message: str, state: GraphState) -> bool:
    resolution = state.get("context_resolution") or {}
    if resolution.get("resolved_reference") not in {None, "", "none"}:
        return False

    normalized = message.strip().lower()
    has_reference = any(keyword in normalized for keyword in _CONTEXT_DEPENDENT_REFERENCES)
    has_plan_context = (
        _has_pending_plan_confirmation_v2(state)
        or bool(state.get("proposed_plan"))
        or _has_recent_plan_intent(state)
        or bool((state.get("recent_dialogue") or {}).get("recent_turns"))
        or state.get("intent") in {INTENT_INFO, INTENT_RECORD}
        or state.get("previous_intent") in {INTENT_INFO, INTENT_RECORD}
    )
    return has_reference and not has_plan_context
