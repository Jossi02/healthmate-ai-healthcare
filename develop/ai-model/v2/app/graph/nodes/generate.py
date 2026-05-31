"""Draft generation node for responses and proposed plans."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta

from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.core.intents import (
    INTENT_APPROVAL,
    INTENT_CARE,
    INTENT_CASUAL,
    INTENT_INFO,
    INTENT_MODIFY,
    INTENT_PLAN,
    INTENT_RECORD,
    INTENT_SAFETY,
)
from app.core.persona_registry import resolve_persona
from app.core.persona_style import (
    apply_persona_signature,
    dedupe_repeated_sentences,
    looks_mostly_english,
    normalize_plan_flow_preview,
    selected_persona_id,
    strip_plan_flow_preamble,
)
from app.core.prompt_loader import compose_prompts, load_prompt
from app.graph.deps import NodeDeps
from app.schemas.home import HomeRecommendationResponse
from app.schemas.llm_responses import DraftResponse, SelfEvalResponse
from app.schemas.state import DraftComponents, GraphState
from app.services.home_recommendations import (
    PROMPT_PATH as _HOME_RECOMMENDATION_PROMPT,
    build_home_recommendation_prompt_input,
    empty_home_recommendations,
    kst_today_iso,
    normalize_home_recommendations,
    validate_home_recommendation_profile_fit,
)

logger = logging.getLogger(__name__)

_MENTAL_HEALTH_SAFETY_PATTERNS = re.compile(
    r"자해|자살|죽고\s*싶|극단적\s*선택|충동|해치고\s*싶|살고\s*싶지",
)
_PHYSICAL_SAFETY_PATTERNS = re.compile(
    r"가슴.*조여|가슴.*조이|숨이?\s*차|호흡.*힘들|어지럽|쓰러질\s*것\s*같|실신|기절|"
    r"과다\s*복용|심한\s*통증|출혈|피가\s*멈추지",
)
_EXTREME_DIET_SAFETY_PATTERNS = re.compile(
    r"굶는?\s*식단|굶어서|단식.*살|물만\s*마시|물만.*식단|"
    r"일주일.*[5-9]\s*kg|[5-9]\s*kg.*일주일|[5-9]\s*kg.*빨리|빨리.*[5-9]\s*kg|"
    r"극단적.*다이어트|초저칼로리|(?:[1-9]\d{2}|1000)\s*(?:kcal|칼로리)",
)

MAX_SELF_EVAL = 1
_SELF_EVAL_INTENTS = {INTENT_SAFETY, INTENT_CARE}

_PLAN_TYPE_KEYWORDS = {
    "diet": ("식단", "식사", "영양", "칼로리", "다이어트", "meal", "diet", "nutrition", "calorie"),
    "workout": ("운동", "러닝", "달리기", "헬스", "근력", "유산소", "스트레칭", "산책", "웨이트", "exercise", "workout", "training", "run"),
}
_WORKOUT_CATEGORY_LABELS = {
    "stretching": "스트레칭",
    "cardio": "유산소",
    "upper_body": "상체",
    "lower_body": "하체",
}
_WORKOUT_CATEGORY_KEYWORDS = {
    "stretching": (
        "스트레칭",
        "stretch",
        "mobility",
        "가동성",
        "요가",
        "폼롤",
        "햄스트링",
        "고양이",
    ),
    "cardio": (
        "유산소",
        "cardio",
        "걷기",
        "walking",
        "walk",
        "러닝",
        "run",
        "달리기",
        "자전거",
        "bike",
        "사이클",
        "treadmill",
        "트레드밀",
        "제자리",
        "인터벌",
    ),
    "upper_body": (
        "상체",
        "upper",
        "푸시업",
        "푸쉬업",
        "push",
        "로우",
        "row",
        "밴드",
        "어깨",
        "가슴",
        "등",
        "팔",
        "벤치",
        "풀업",
    ),
    "lower_body": (
        "하체",
        "lower",
        "스쿼트",
        "squat",
        "런지",
        "lunge",
        "leg",
        "bridge",
        "브릿지",
        "둔근",
        "엉덩",
        "햄스트링",
        "종아리",
    ),
}

_DRAFT_COMMON_PROMPT = "nodes/generate/draft_common.md"
_DRAFT_DEFAULT_PROMPT = "nodes/generate/draft_default.md"
_DRAFT_PROMPTS_BY_INTENT = {
    INTENT_PLAN: "nodes/generate/draft_plan.md",
    INTENT_MODIFY: "nodes/generate/draft_modify.md",
    INTENT_INFO: "nodes/generate/draft_info.md",
    INTENT_CARE: "nodes/generate/draft_care.md",
    INTENT_SAFETY: "nodes/generate/draft_safety.md",
}

_SELF_EVAL_PROMPT = load_prompt("nodes/generate/self_eval.md")

_SHORT_TERM_MEMORY_PROMPT = """Short-term memory mode:
- Answer from the recent chat history first.
- Prefer the user's earlier statements over generic health advice.
- Do not use search evidence, profile metadata, or plan context unless the recent chat itself mentions them.
- If the answer is not present in the recent chat history, say that you cannot confirm it from the recent conversation.
- Keep the answer direct and specific to the recall question.
"""

_ANTI_REPETITION_PROMPT = """Anti-repetition mode:
- Do not repeat the previous assistant response in the same or slightly different wording.
- If the user asks a follow-up, answer only the new or directly requested point.
- Avoid re-listing the same reasons or plan summary unless the user explicitly asks for them again.
"""

_STARTER_PLAN_PROMPT = """Starter plan mode:
- The user asked for a plan or plan modification, so do not return only a questionnaire.
- If profile information is sparse, still propose a safe low-risk starter plan that can be refined later.
- Use conservative assumptions: beginner-friendly, sustainable volume, low-to-moderate intensity.
- Ask at most one short follow-up after presenting the starter plan.
- Leave proposed_plan empty only when the request is truly ambiguous or safety-critical details are missing.
"""

_SHORT_TERM_MEMORY_RECENT_LIMIT = 8
_REPETITION_OVERLAP_THRESHOLD = 0.8
_RECENT_DIALOGUE_HISTORY_LIMIT = 4


def make_generate_node(deps: NodeDeps):
    async def generate_node(state: GraphState) -> dict:
        if state.get("response") and not state.get("force_regenerate"):
            return {}

        started_at = time.perf_counter()
        intent = state.get("intent", "")
        eval_count = state.get("self_eval_count", 0)
        failure_reason = state.get("self_eval_failure_reason")
        deps.trace.record_current_event(
            stage="generate",
            status="info",
            title="Draft generation started",
            detail={"intent": intent, "self_eval_count": eval_count},
        )

        if state.get("request_kind") == "home_recommendation":
            return await _generate_home_recommendations(deps, state, started_at)

        if intent == INTENT_SAFETY:
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Safety draft shortcut used",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            safety_draft = _build_safety_draft(state)
            safety_components, _ = _apply_profile_quality_guardrails(
                safety_draft["draft_components"],
                [],
                None,
                state,
            )
            safety_draft["draft_components"] = safety_components
            safety_draft["draft_response"] = render_draft_preview(safety_components)
            return _finalize_persona_aware_response(deps, state, safety_draft)

        if intent == INTENT_APPROVAL:
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Approval draft shortcut used",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, _build_approval_draft_v2(state))

        if intent == INTENT_CARE:
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Care draft shortcut used",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, _build_care_draft(state))

        if intent == INTENT_CASUAL:
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Casual draft shortcut used",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, _build_casual_draft(state))

        if intent == INTENT_RECORD and state.get("record_type") == "plan_delete":
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Plan delete draft shortcut used",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, _build_plan_delete_draft(state))

        direct_memory_draft = _build_direct_short_term_memory_draft(state)
        if direct_memory_draft is not None:
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Direct short-term memory draft used",
                detail={"intent": intent},
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, direct_memory_draft)

        direct_past_memory_draft = _build_direct_past_memory_draft(state)
        if direct_past_memory_draft is not None:
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Direct past-memory draft used",
                detail={"intent": intent, "memory_results": len(_memory_results(state))},
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, direct_past_memory_draft)

        if intent in {INTENT_PLAN, INTENT_MODIFY} and _is_mixed_plan_type_request(_resolved_user_message(state)):
            deps.trace.record_current_event(
                stage="generate",
                status="ok",
                title="Mixed workout/diet plan request clarified",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return _finalize_persona_aware_response(deps, state, _build_mixed_plan_clarification_draft())

        context = _build_draft_context(state)
        system_prompt = _build_draft_system_prompt(state, failure_reason)

        try:
            draft_result = await _request_draft_with_guardrails(
                deps=deps,
                state=state,
                system_prompt=system_prompt,
                user_content=context,
                failure_reason=failure_reason,
            )
            draft_components = _build_components_from_result(draft_result, state)
            draft_text = render_draft_preview(draft_components)
            proposed_plan = [
                item.model_dump() for item in draft_result.proposed_plan
            ] if draft_result.proposed_plan else []
            proposed_plan_type = _resolve_proposed_plan_type(state, draft_result, proposed_plan)
            proposed_plan_action = _resolve_proposed_plan_action(state, proposed_plan)
        except Exception as exc:
            logger.error("Draft generation failed: %s", exc)
            deps.trace.record_current_alert(
                severity="error",
                message="Draft generation failed",
                detail={"intent": intent, "error": str(exc)},
            )
            draft_components = normalize_draft_components(
                None,
                fallback_text="초안을 생성하는 중 오류가 발생했습니다. 다시 시도해 주세요.",
            )
            draft_text = render_draft_preview(draft_components)
            proposed_plan = []
            proposed_plan_type = None
            proposed_plan_action = None

        if intent not in {INTENT_PLAN, INTENT_MODIFY}:
            proposed_plan = []
            proposed_plan_type = None
            proposed_plan_action = None

        if not proposed_plan and intent == INTENT_APPROVAL:
            proposed_plan = state.get("proposed_plan")
            proposed_plan_type = state.get("proposed_plan_type")
            proposed_plan_action = state.get("proposed_plan_action")

        if not proposed_plan and intent == INTENT_PLAN:
            (
                draft_components,
                draft_text,
                proposed_plan,
                proposed_plan_type,
                proposed_plan_action,
            ) = _build_starter_plan_fallback(state)
            deps.trace.record_current_alert(
                severity="warning",
                message="Create draft returned no structured plan; starter fallback applied",
                detail={"domain": proposed_plan_type},
            )

        if not proposed_plan and intent == INTENT_MODIFY:
            (
                draft_components,
                draft_text,
                proposed_plan,
                proposed_plan_type,
                proposed_plan_action,
            ) = _build_modify_plan_fallback(state)
            if proposed_plan:
                deps.trace.record_current_alert(
                    severity="warning",
                    message="Modify draft returned no structured plan; active proposal fallback applied",
                    detail={"domain": proposed_plan_type},
                )

        _record_evidence_integration(
            deps,
            state,
            draft_components,
            proposed_plan,
            proposed_plan_type,
        )

        if intent in {INTENT_PLAN, INTENT_MODIFY}:
            guard_started_at = time.perf_counter()
            proposed_plan = _sanitize_plan_data_layer(proposed_plan)
            if _plan_contract_needs_fallback(proposed_plan, proposed_plan_type):
                if intent == INTENT_PLAN:
                    (
                        draft_components,
                        draft_text,
                        proposed_plan,
                        proposed_plan_type,
                        proposed_plan_action,
                    ) = _build_starter_plan_fallback(state)
                else:
                    (
                        draft_components,
                        draft_text,
                        proposed_plan,
                        proposed_plan_type,
                        proposed_plan_action,
                    ) = _build_modify_plan_fallback(state)
                deps.trace.record_current_alert(
                    severity="warning",
                    message="Draft plan failed write contract; deterministic fallback applied",
                    detail={"domain": proposed_plan_type, "count": len(proposed_plan or [])},
                )
            draft_components, proposed_plan = _apply_profile_quality_guardrails(
                draft_components,
                proposed_plan,
                proposed_plan_type,
                state,
            )
            if proposed_plan_type == "diet" and _diet_plan_requires_safe_fallback(
                proposed_plan,
                _effective_user_profile(state),
                state.get("user_message"),
            ):
                (
                    draft_components,
                    draft_text,
                    proposed_plan,
                    proposed_plan_type,
                    proposed_plan_action,
                ) = _build_starter_plan_fallback({**state, "domain": "diet"})
                proposed_plan = _sanitize_plan_data_layer(proposed_plan)
                draft_components, proposed_plan = _apply_profile_quality_guardrails(
                    draft_components,
                    proposed_plan,
                    proposed_plan_type,
                    state,
                )
                deps.trace.record_current_alert(
                    severity="warning",
                    message="Diet draft conflicted with dietary constraints; deterministic fallback applied",
                    detail={"count": len(proposed_plan or [])},
                )
            deps.trace.record_current_event(
                stage="generate.profile_safety_validator",
                status="ok",
                title="Profile guardrails applied",
                detail={
                    "proposed_plan_count": len(proposed_plan or []),
                    "proposed_plan_type": proposed_plan_type,
                    "hard_profile_constraints": (state.get("profile_constraints") or {}).get("hard_profile_constraints") or [],
                    "request_hard_constraints": (state.get("profile_constraints") or {}).get("request_hard_constraints") or [],
                    "safety_risks": (state.get("profile_constraints") or {}).get("safety_risks") or [],
                },
                duration_ms=round((time.perf_counter() - guard_started_at) * 1000, 2),
            )
            normalize_started_at = time.perf_counter()
            proposed_plan = _expand_long_range_plan_if_requested(
                state,
                proposed_plan,
                proposed_plan_type,
            )
            if proposed_plan:
                draft_components = _normalize_plan_core_message(
                    draft_components,
                    proposed_plan_type,
                    proposed_plan_action,
                )
                draft_components["plan_preview"] = _render_plan_preview_from_items(proposed_plan)
                draft_components = _normalize_plan_approval_question(
                    draft_components,
                    proposed_plan_type,
                    proposed_plan_action,
                )
                draft_components = _minimize_plan_exposition(
                    draft_components,
                    proposed_plan_type,
                )
            draft_text = render_draft_preview(draft_components)
            deps.trace.record_current_event(
                stage="generate.plan_normalizer",
                status="ok",
                title="Plan normalized for display/write contract",
                detail={
                    "proposed_plan_count": len(proposed_plan or []),
                    "proposed_plan_type": proposed_plan_type,
                    "days": _plan_unique_iso_days(proposed_plan or []),
                    "approval_question_present": bool(draft_components.get("approval_question")),
                },
                duration_ms=round((time.perf_counter() - normalize_started_at) * 1000, 2),
            )
        elif intent == INTENT_INFO:
            draft_components = _apply_info_profile_guardrails(draft_components, state)
            draft_text = render_draft_preview(draft_components)

        deps.trace.record_current_event(
            stage="generate.answer_composer",
            status="ok",
            title="Answer draft composed",
            detail={
                "intent": intent,
                "response_length": len(draft_text),
                "has_plan_preview": bool(draft_components.get("plan_preview")),
                "reason_count": len(draft_components.get("reason_points") or []),
                "safety_note_count": len(draft_components.get("safety_notes") or []),
            },
        )

        if intent in _SELF_EVAL_INTENTS:
            passed, reason = await _self_evaluate(deps, state, draft_text)
            if not passed:
                if eval_count < MAX_SELF_EVAL:
                    logger.info("Self-eval failed, retrying: count=%d reason=%s", eval_count, reason)
                    deps.trace.record_current_alert(
                        severity="warning",
                        message="Self-eval requested another generation pass",
                        detail={"reason": reason, "next_count": eval_count + 1},
                    )
                    return {
                        "self_eval_count": eval_count + 1,
                        "self_eval_failure_reason": reason,
                    }
                logger.warning("Self-eval retry limit reached, applying partial patch")
                deps.trace.record_current_alert(
                    severity="warning",
                    message="Self-eval retry limit reached; partial patch applied",
                    detail={"reason": reason},
                )
                draft_components = _apply_partial_patch(draft_components, reason)
                draft_text = render_draft_preview(draft_components)

        deps.trace.record_current_event(
            stage="generate",
            status="ok",
            title="Draft generation completed",
            detail={
                "intent": intent,
                "proposed_plan_count": len(proposed_plan or []),
                "proposed_plan_type": proposed_plan_type,
                "proposed_plan_action": proposed_plan_action,
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        result = {
            "draft_response": draft_text,
            "draft_components": draft_components,
            "proposed_plan": proposed_plan,
            "proposed_plan_type": proposed_plan_type,
            "proposed_plan_action": proposed_plan_action,
            "force_regenerate": False,
            "self_eval_count": 0,
            "self_eval_failure_reason": None,
        }
        if intent in {INTENT_PLAN, INTENT_MODIFY} and proposed_plan:
            result["awaiting_plan_confirmation"] = True
        return _finalize_persona_aware_response(deps, state, result)

    return generate_node


def _persona_context(state: GraphState) -> tuple[str | None, str, object | None]:
    profile = _effective_user_profile(state)
    selected_persona = selected_persona_id(profile)
    resolved_persona_id, persona_path = resolve_persona(selected_persona)
    return selected_persona, resolved_persona_id, persona_path


def _build_persona_generation_prompt(state: GraphState) -> str:
    selected_persona, resolved_persona_id, persona_path = _persona_context(state)
    profile = _effective_user_profile(state)
    emotion = state.get("emotion") or {}
    emotion_label = emotion.get("label", "neutral")
    emotion_intensity = float(emotion.get("intensity", 0))
    emotion_str = f"{emotion_label} (intensity {emotion_intensity:.1f})"

    try:
        template = persona_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
        persona_prompt = template.format(
            persona_id=resolved_persona_id,
            emotion=emotion_str,
            mbti=profile.get("mbti", "unknown"),
            intimacy_level=state.get("intimacy_level", 1),
        )
    except Exception as exc:
        logger.warning("Persona prompt load failed in generate node: %s", exc)
        persona_prompt = (
            f"Use the selected FitUs persona style: {resolved_persona_id}. "
            "Keep the answer Korean, concise, and result-first."
        )

    guardrails = [
        "Persona-aware generation mode:",
        "- Apply the persona style inside the DraftResponse text fields now; there is no later persona rewrite node.",
        "- Persona affects wording, warmth, and encouragement only. It must not change intent, domain, dates, evidence, hard profile constraints, or proposed_plan data.",
        "- Treat proposed_plan as a neutral data layer. Do not put persona catchphrases, roleplay, emotional coaching, or character wording inside proposed_plan.name, detail, exercise_name, day, sets, duration_minutes, or calories.",
        "- Persona style may appear only in short user-facing prose fields such as core_message and approval_question, and it must never add, remove, or rename foods/exercises/durations/sets.",
        "- Keep the concrete result first. Do not add a greeting, catchphrase, meta setup, or long emotional preface before the result.",
        "- For plan create/modify, keep workout and diet structurally separate and keep rationale short unless the user asks why.",
        "- Do not add explanatory phrases such as 'allergy considered', 'restriction reflected', or 'disease considered' in plan answers unless safety requires it.",
        "- The response must still satisfy the DraftResponse JSON schema exactly.",
        f"- Selected persona id: {selected_persona or 'default'}; resolved persona id: {resolved_persona_id}.",
    ]
    return "\n".join(guardrails) + "\n\n" + persona_prompt


def _effective_user_profile(state: GraphState) -> dict:
    return dict(state.get("effective_user_profile") or state.get("user_profile") or {})


def _finalize_persona_aware_response(deps: NodeDeps, state: GraphState, result: dict) -> dict:
    payload = dict(result)
    render_state = _response_render_state(state, payload)
    selected_persona, resolved_persona_id, _ = _persona_context(state)
    original_plan_snapshot = _canonical_plan_payload(payload.get("proposed_plan"))
    persona_marker_hits = _plan_persona_marker_hits(payload.get("proposed_plan") or [])
    quality_flags = dict(payload.get("generation_quality_flags") or {})
    if persona_marker_hits:
        quality_flags["plan_persona_marker_hits"] = persona_marker_hits[:8]
    else:
        quality_flags.pop("plan_persona_marker_hits", None)
    draft_components = normalize_draft_components(
        payload.get("draft_components"),
        fallback_text=payload.get("draft_response"),
    )
    draft_response = render_draft_preview(draft_components)
    final_response = draft_response

    final_response = strip_plan_flow_preamble(final_response, render_state)
    final_response = normalize_plan_flow_preview(
        final_response,
        render_state,
        draft_components,
        resolved_persona_id,
    )
    if render_state.get("intent") in {INTENT_PLAN, INTENT_MODIFY, INTENT_APPROVAL}:
        final_response = dedupe_repeated_sentences(final_response)
    final_response = apply_persona_signature(final_response, resolved_persona_id, render_state)
    mutation_report = _persona_mutation_report(
        original_plan_snapshot,
        _canonical_plan_payload(payload.get("proposed_plan")),
        draft_response,
        final_response,
    )
    style_report = _persona_style_report(final_response, render_state, resolved_persona_id)
    mutation_report["plan_persona_marker_hit_count"] = len(persona_marker_hits)
    mutation_report["plan_persona_marker_hits"] = persona_marker_hits[:6]
    if style_report["violations"]:
        quality_flags["persona_style_violations"] = style_report["violations"]
    else:
        quality_flags.pop("persona_style_violations", None)

    payload["draft_components"] = draft_components
    payload["draft_response"] = draft_response
    payload["response"] = final_response
    payload["resolved_persona_id"] = resolved_persona_id
    payload["generation_quality_flags"] = quality_flags or None
    payload["force_regenerate"] = False

    deps.trace.record_current_event(
        stage="generate.persona_renderer",
        status="ok",
        title="Persona-aware response finalized",
        detail={
            "selected_persona_id": selected_persona,
            "resolved_persona_id": resolved_persona_id,
            "response_length": len(final_response),
            "draft_response_length": len(draft_response),
            "plan_data_unchanged": mutation_report["plan_data_unchanged"],
            "persona_style_violation_count": len(style_report["violations"]),
        },
    )
    deps.trace.record_current_event(
        stage="persona",
        status="ok",
        title="Persona style applied",
        detail={
            "selected_persona_id": selected_persona,
            "resolved_persona_id": resolved_persona_id,
            "plan_data_unchanged": mutation_report["plan_data_unchanged"],
        },
    )
    deps.trace.record_current_event(
        stage="generate.persona_mutation_check",
        status="ok" if mutation_report["plan_data_unchanged"] else "warn",
        title="Persona mutation guard checked",
        detail={**mutation_report, "style_report": style_report},
    )
    return payload


def _response_render_state(state: GraphState, payload: dict) -> GraphState:
    render_state = dict(state)
    for key in ("proposed_plan", "proposed_plan_type", "proposed_plan_action"):
        if payload.get(key) is not None:
            render_state[key] = payload.get(key)
    if payload.get("proposed_plan_type") in {"workout", "diet"}:
        render_state["domain"] = payload["proposed_plan_type"]
    return render_state


def _record_evidence_integration(
    deps: NodeDeps,
    state: GraphState,
    draft_components: DraftComponents,
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
) -> None:
    search_results = state.get("search_results") or []
    retrieval_decision = state.get("retrieval_decision") or {}
    profile_constraints = state.get("profile_constraints") or {}
    returned_kb_ids = [
        str(result.get("kb_id") or (result.get("metadata") or {}).get("kb_id") or "")
        for result in search_results[:8]
    ]
    deps.trace.record_current_event(
        stage="generate.evidence_integrator",
        status="ok",
        title="Retrieval evidence integration checked",
        detail={
            "requires_external": bool(retrieval_decision.get("requires_external")),
            "search_quality": state.get("search_quality"),
            "search_results_count": len(search_results),
            "returned_kb_ids": [kb_id for kb_id in returned_kb_ids if kb_id],
            "grounding_summary_present": bool(str(draft_components.get("search_grounding_summary") or "").strip()),
            "proposed_plan_count": len(proposed_plan or []),
            "proposed_plan_type": proposed_plan_type,
            "retrieval_constraints": profile_constraints.get("retrieval_constraints") or [],
            "retrieval_critical_constraints": profile_constraints.get("retrieval_critical_constraints") or [],
        },
    )


def _canonical_plan_payload(plan: object) -> str:
    try:
        return json.dumps(plan or [], ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(plan or [])


def _persona_mutation_report(
    original_plan_snapshot: str,
    final_plan_snapshot: str,
    draft_response: str,
    final_response: str,
) -> dict[str, object]:
    return {
        "plan_data_unchanged": original_plan_snapshot == final_plan_snapshot,
        "draft_response_length": len(draft_response or ""),
        "final_response_length": len(final_response or ""),
        "length_delta": len(final_response or "") - len(draft_response or ""),
    }


def _persona_style_report(final_response: str, state: GraphState, persona_id: str) -> dict[str, object]:
    text = str(final_response or "").strip()
    intent = str(state.get("intent") or "")
    lines = [line for line in text.splitlines() if line.strip()]
    violations: list[dict[str, object]] = []

    if intent in {INTENT_PLAN, INTENT_MODIFY} and len(text) > 900:
        violations.append(
            {
                "code": "persona_plan_response_too_long",
                "severity": "warning",
                "length": len(text),
                "limit": 900,
            }
        )
    if intent == INTENT_APPROVAL and len(text) > 280:
        violations.append(
            {
                "code": "persona_approval_response_too_long",
                "severity": "warning",
                "length": len(text),
                "limit": 280,
            }
        )
    if intent in {INTENT_PLAN, INTENT_MODIFY, INTENT_APPROVAL} and len(lines) > 18:
        violations.append(
            {
                "code": "persona_plan_response_too_many_lines",
                "severity": "warning",
                "line_count": len(lines),
                "limit": 18,
            }
        )
    if looks_mostly_english(text):
        violations.append(
            {
                "code": "persona_response_not_korean_dominant",
                "severity": "warning",
            }
        )

    return {
        "persona_id": persona_id,
        "response_length": len(text),
        "line_count": len(lines),
        "violations": violations,
    }


def _sanitize_plan_data_layer(proposed_plan: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for item in proposed_plan or []:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        for key in ("name", "detail", "day"):
            if key in next_item:
                next_item[key] = _compact_plan_data_text(next_item.get(key))
        exercises: list[dict] = []
        for exercise in next_item.get("ex_list") or []:
            if not isinstance(exercise, dict):
                continue
            next_exercise = dict(exercise)
            if "exercise_name" in next_exercise:
                next_exercise["exercise_name"] = _compact_plan_data_text(next_exercise.get("exercise_name"))
            for numeric_key in ("sets", "duration_minutes", "calories"):
                if numeric_key in next_exercise:
                    parsed_value = _safe_int(next_exercise.get(numeric_key))
                    if parsed_value is None:
                        if numeric_key == "calories":
                            next_exercise[numeric_key] = 0
                        else:
                            next_exercise.pop(numeric_key, None)
                    else:
                        next_exercise[numeric_key] = parsed_value
            exercises.append(next_exercise)
        next_item["ex_list"] = exercises
        cleaned.append(next_item)
    return cleaned


_PLAN_DATA_PERSONA_MARKERS = (
    "cheer_sis",
    "soft_senior",
    "strict_trainer",
    "science_coach",
    "playful_buddy",
    "daily_manager",
    "persona",
    "누나",
    "언니",
    "스파르타",
    "화이팅",
    "파이팅",
    "가보자",
    "좋아,",
    "괜찮아",
    "괜찮아요",
)


def _plan_persona_marker_hits(proposed_plan: list[dict]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for item_index, item in enumerate(proposed_plan or []):
        if not isinstance(item, dict):
            continue
        for field in ("name", "detail", "day"):
            marker = _first_plan_persona_marker(item.get(field))
            if marker:
                hits.append(
                    {
                        "path": f"{item_index}.{field}",
                        "marker": marker,
                        "text": _compact_plan_data_text(item.get(field))[:120],
                    }
                )
        for exercise_index, exercise in enumerate(item.get("ex_list") or []):
            if not isinstance(exercise, dict):
                continue
            marker = _first_plan_persona_marker(exercise.get("exercise_name"))
            if marker:
                hits.append(
                    {
                        "path": f"{item_index}.ex_list.{exercise_index}.exercise_name",
                        "marker": marker,
                        "text": _compact_plan_data_text(exercise.get("exercise_name"))[:120],
                    }
                )
    return hits


def _first_plan_persona_marker(value: object) -> str | None:
    text = _compact_plan_data_text(value).lower()
    if not text:
        return None
    for marker in _PLAN_DATA_PERSONA_MARKERS:
        if marker.lower() in text:
            return marker
    return None


def _compact_plan_data_text(value: object) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


async def _generate_home_recommendations(
    deps: NodeDeps,
    state: GraphState,
    started_at: float,
) -> dict:
    scope = state.get("home_recommendation_scope") or "all"
    date = kst_today_iso()
    effective_profile = _effective_user_profile(state)

    try:
        raw = await deps.router.generate(
            system_prompt=load_prompt(_HOME_RECOMMENDATION_PROMPT),
            user_content=build_home_recommendation_prompt_input(
                date=date,
                scope=scope,
                user_profile=effective_profile,
                today_plan=state.get("today_plan") or [],
                recent_recommendations=state.get("home_recommendation_recent") or {},
            ),
            response_schema=HomeRecommendationResponse,
        )
        result = HomeRecommendationResponse.model_validate_json(raw)
        normalized = normalize_home_recommendations(
            result,
            scope=scope,
            date=date,
            user_profile=effective_profile,
            today_plan=state.get("today_plan") or [],
            recent_recommendations=state.get("home_recommendation_recent") or {},
        )
    except Exception as exc:
        logger.error("Home recommendation generation failed: %s", exc)
        deps.trace.record_current_alert(
            severity="error",
            message="Home recommendation generation failed",
            detail={"scope": scope, "error": str(exc)},
        )
        normalized = empty_home_recommendations(
            date=date,
            scope=scope,
            user_profile=effective_profile,
            today_plan=state.get("today_plan") or [],
            recent_recommendations=state.get("home_recommendation_recent") or {},
        )

    profile_fit_issues = validate_home_recommendation_profile_fit(
        normalized,
        user_profile=effective_profile,
    )
    deps.trace.record_current_event(
        stage="home_recommendation.profile_guard",
        status="warn" if profile_fit_issues else "ok",
        title="Home recommendation profile fit checked",
        detail={
            "scope": scope,
            "profile_keys": sorted(
                key
                for key, value in effective_profile.items()
                if value not in (None, "", [], {}, "[]")
            ),
            "issue_count": len(profile_fit_issues),
            "issues": profile_fit_issues[:8],
        },
    )
    if profile_fit_issues:
        deps.trace.record_current_alert(
            severity="warning",
            message="Home recommendation profile guard repaired unsafe slots",
            detail={"scope": scope, "issues": profile_fit_issues[:8]},
        )
        normalized = empty_home_recommendations(
            date=date,
            scope=scope,
            user_profile=effective_profile,
            today_plan=state.get("today_plan") or [],
            recent_recommendations=state.get("home_recommendation_recent") or {},
        )
        profile_fit_issues = validate_home_recommendation_profile_fit(
            normalized,
            user_profile=effective_profile,
        )
        deps.trace.record_current_event(
            stage="home_recommendation.profile_guard.recheck",
            status="warn" if profile_fit_issues else "ok",
            title="Home recommendation deterministic fallback rechecked",
            detail={
                "scope": scope,
                "issue_count": len(profile_fit_issues),
                "issues": profile_fit_issues[:8],
            },
        )

    deps.trace.record_current_event(
        stage="generate",
        status="ok",
        title="Home recommendations generated",
        detail={
            "scope": scope,
            "workout_slots": sum(
                1
                for item in normalized.workout.model_dump().values()
                if item is not None
            ),
            "diet_slots": sum(
                1
                for item in normalized.diet.model_dump().values()
                if item is not None
            ),
            "profile_fit_issue_count": len(profile_fit_issues),
        },
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )
    return {
        "home_recommendations": normalized.model_dump(),
        "generation_quality_flags": {
            "home_profile_fit_issue_count": len(profile_fit_issues),
            "home_profile_fit_issues": profile_fit_issues[:8],
        },
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


async def _request_draft_with_guardrails(
    deps: NodeDeps,
    state: GraphState,
    system_prompt: str,
    user_content: str,
    failure_reason: str | None,
) -> DraftResponse:
    started_at = time.perf_counter()
    raw = await deps.router.generate(
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=DraftResponse,
    )
    draft_result = DraftResponse.model_validate_json(raw)
    deps.trace.record_current_event(
        stage="generate.plan_synthesizer",
        status="ok",
        title="Structured draft synthesized",
        detail={
            "intent": state.get("intent"),
            "domain": state.get("domain"),
            "proposed_plan_count": len(draft_result.proposed_plan or []),
            "proposed_plan_type": draft_result.proposed_plan_type,
            "search_results_count": len(state.get("search_results") or []),
        },
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )

    if not _needs_generate_retry(state, draft_result):
        return draft_result

    deps.trace.record_current_event(
        stage="generate",
        status="warning",
        title="Draft regeneration requested",
        detail={
            "short_term_memory_query": bool(state.get("short_term_memory_query")),
            "last_assistant_present": bool(_latest_assistant_reference(state)),
        },
    )

    retry_raw = await deps.router.generate(
        system_prompt=_build_draft_system_prompt(
            state,
            failure_reason,
            reinforce_short_term=True,
            avoid_repetition=True,
            force_starter_plan=_should_retry_for_missing_plan(state, draft_result),
        ),
        user_content=_build_draft_context(
            state,
            force_short_term=bool(state.get("short_term_memory_query")),
        ),
        response_schema=DraftResponse,
    )
    retry_result = DraftResponse.model_validate_json(retry_raw)
    deps.trace.record_current_event(
        stage="generate.plan_synthesizer",
        status="ok",
        title="Structured draft resynthesized",
        detail={
            "intent": state.get("intent"),
            "domain": state.get("domain"),
            "proposed_plan_count": len(retry_result.proposed_plan or []),
            "proposed_plan_type": retry_result.proposed_plan_type,
        },
    )
    return retry_result


def _build_draft_context(state: GraphState, *, force_short_term: bool = False) -> str:
    if force_short_term or state.get("short_term_memory_query"):
        return _build_short_term_memory_context(state)

    parts: list[str] = []
    parts.append(f"[오늘 날짜]\n{kst_today_iso()}")

    recent_dialogue = _recent_dialogue_history(state)
    if recent_dialogue:
        parts.append(f"[Recent Dialogue]\n{recent_dialogue}")

    resolved_user_message = _resolved_user_message(state)
    parts.append(f"[현재 질문]\n{resolved_user_message}")
    if resolved_user_message != state["user_message"]:
        parts.append(f"[Original User Message]\n{state['user_message']}")

    results = state.get("search_results") or []
    if results:
        snippets = "\n".join(
            f"[{result.get('source', 'unknown')}] {result.get('text', '')[:200]}"
            for result in results[: _search_snippet_limit(state)]
        )
        parts.append(f"[참고 정보]\n{snippets}")

    modify_context = state.get("modify_plan_context")
    if modify_context:
        parts.append(f"[현재 전체 플랜]\n{json.dumps(modify_context, ensure_ascii=False)[:500]}")
    else:
        active_proposal = state.get("active_proposal") or {}
        proposal_summary = str(active_proposal.get("summary") or "").strip()
        if proposal_summary:
            parts.append(f"[Active Proposal]\n{proposal_summary[:200]}")

    profile = _effective_user_profile(state)
    if profile:
        profile_copy = profile.copy()
        profile_copy.pop("mbti", None)
        parts.append(f"[사용자 프로필]\n{json.dumps(profile_copy, ensure_ascii=False)}")

    profile_constraints = state.get("profile_constraints")
    if profile_constraints:
        parts.append(
            "[Compiled Profile Constraints]\n"
            + json.dumps(
                {
                    "constraints": profile_constraints.get("constraints") or [],
                    "hard_profile_constraints": profile_constraints.get("hard_profile_constraints") or [],
                    "request_hard_constraints": profile_constraints.get("request_hard_constraints") or [],
                    "retrieval_constraints": profile_constraints.get("retrieval_constraints") or [],
                    "query_constraints": profile_constraints.get("query_constraints") or [],
                    "critical_constraints": profile_constraints.get("critical_constraints") or [],
                    "retrieval_critical_constraints": profile_constraints.get("retrieval_critical_constraints") or [],
                    "goals": profile_constraints.get("goals") or [],
                    "safety_risks": profile_constraints.get("safety_risks") or [],
                    "profile_field_coverage": profile_constraints.get("profile_field_coverage") or {},
                    "summary": profile_constraints.get("summary") or {},
                },
                ensure_ascii=False,
            )
        )

    changes = state.get("profile_changes")
    if changes:
        parts.append(f"[프로필 변경 요청]\n{json.dumps(changes, ensure_ascii=False)}")

    return "\n\n".join(parts)


def _build_short_term_memory_context(state: GraphState) -> str:
    parts = [f"[Today]\n{kst_today_iso()}"]

    dialogue_history = _recent_dialogue_history(state, limit=_SHORT_TERM_MEMORY_RECENT_LIMIT)
    if dialogue_history:
        parts.append(f"[Recent Chat History]\n{dialogue_history}")

    last_assistant_message = _latest_assistant_reference(state)
    if last_assistant_message:
        parts.append(f"[Previous Assistant Response]\n{last_assistant_message[:400]}")

    parts.append(f"[Current Recall Question]\n{_resolved_user_message(state)}")
    parts.append(
        "[Instruction]\nUse only the recent chat history above. "
        "If the answer is missing there, say you cannot confirm it from the recent conversation."
    )
    return "\n\n".join(parts)


def _search_snippet_limit(state: GraphState) -> int:
    if state.get("intent") == INTENT_INFO:
        return 2
    return 3


def _build_draft_system_prompt(
    state: GraphState,
    failure_reason: str | None,
    *,
    reinforce_short_term: bool = False,
    avoid_repetition: bool = False,
    force_starter_plan: bool = False,
) -> str:
    intent = state.get("intent", "")
    intent_prompt = _DRAFT_PROMPTS_BY_INTENT.get(intent, _DRAFT_DEFAULT_PROMPT)

    sections = [
        compose_prompts(_DRAFT_COMMON_PROMPT, intent_prompt),
        _build_persona_generation_prompt(state),
    ]

    emotion = state.get("emotion") or {}
    sections.append(
        f"현재 사용자 감정: {emotion.get('label', '중립')} (강도 {emotion.get('intensity', 0.0):.1f})"
    )
    if state.get("support_mode") == "care":
        sections.append(
            "Support mode is care. Keep the task answer intact, but make the tone emotionally supportive and non-judgmental."
        )

    if state.get("search_quality") == "degraded":
        sections.append(
            "주의: 검색 결과가 충분하지 않으므로 일반 원칙 수준으로만 답하고, 근거의 한계를 분명히 드러낸다."
        )

    if failure_reason:
        sections.append(
            f"이전 Draft는 자기 평가를 통과하지 못했다. 실패 이유: {failure_reason}"
        )

    if reinforce_short_term or state.get("short_term_memory_query"):
        sections.append(_SHORT_TERM_MEMORY_PROMPT)

    if force_starter_plan:
        sections.append(_STARTER_PLAN_PROMPT)

    latest_assistant_message = _latest_assistant_reference(state)
    if avoid_repetition or latest_assistant_message:
        sections.append(_ANTI_REPETITION_PROMPT)
        if latest_assistant_message:
            sections.append(
                f"Previous assistant response to avoid repeating:\n{latest_assistant_message[:400]}"
            )

    return "\n\n".join(section for section in sections if section)


def _build_components_from_result(draft_result: DraftResponse, state: GraphState) -> DraftComponents:
    payload = draft_result.model_dump()
    components = normalize_draft_components(payload)
    components["plan_preview"] = _render_plan_preview(draft_result, state)

    if not components["search_grounding_summary"] and state.get("search_results"):
        components["search_grounding_summary"] = "검색 결과를 참고해 핵심 근거만 정리했다."

    return components


def _apply_profile_quality_guardrails(
    components: DraftComponents,
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
    state: GraphState,
) -> tuple[DraftComponents, list[dict]]:
    profile = _effective_user_profile(state)
    patched = normalize_draft_components(dict(components))
    plan = [dict(item) for item in (proposed_plan or [])]

    profile_note = _profile_fit_note(profile)
    if profile_note:
        _append_unique(patched["reason_points"], profile_note)
    memory_note = _memory_grounding_note(state)
    if memory_note:
        _append_unique(patched["reason_points"], memory_note)

    empathy_note = _empathy_note(profile, state)
    if empathy_note:
        if empathy_note not in patched["core_message"]:
            patched["core_message"] = f"{empathy_note} {patched['core_message']}"

    safety_notes = _profile_safety_notes(profile, proposed_plan_type)
    for note in safety_notes:
        _append_unique(patched["safety_notes"], note)

    constraint_note = _constraint_grounding_note(profile, proposed_plan_type)
    if constraint_note:
        if patched["search_grounding_summary"]:
            if constraint_note not in patched["search_grounding_summary"]:
                patched["search_grounding_summary"] = f"{patched['search_grounding_summary']} {constraint_note}"
        else:
            patched["search_grounding_summary"] = constraint_note

    if _has_mixed_workout_diet_items(plan):
        plan = _adjust_mixed_plan_for_profile(plan, profile)
    elif proposed_plan_type == "workout":
        category_note = _workout_category_balance_note(profile)
        if category_note:
            _append_unique(patched["reason_points"], category_note)
        plan = _adjust_workout_plan_for_profile(plan, profile)
    elif proposed_plan_type == "diet":
        plan = _adjust_diet_plan_for_profile(plan, profile)

    return patched, plan


def _apply_info_profile_guardrails(components: DraftComponents, state: GraphState) -> DraftComponents:
    profile = _effective_user_profile(state)
    patched = normalize_draft_components(dict(components))
    patched["approval_question"] = None
    patched["plan_preview"] = ""
    message = str(state.get("user_message") or "")
    constraints = [
        *_as_text_list(profile.get("injury_history")),
        *_as_text_list(profile.get("medical_conditions") or profile.get("conditions")),
        *_as_text_list(profile.get("pain_points")),
        *_as_text_list(profile.get("allergies") or profile.get("dietary_restrictions")),
    ]

    if any(keyword in message for keyword in ("통증", "부상", "아픔", "무릎", "허리", "어깨", "손목", "손가락", "피해야", "내 조건", "내 상황")):
        target = ", ".join(constraints) if constraints else "통증 부위"
        if profile.get("allergies") and not (profile.get("injury_history") or profile.get("pain_points") or profile.get("medical_conditions") or profile.get("conditions")):
            patched["core_message"] = f"{target} 제약이 있으면 해당 재료는 제외하고 안전한 대체 식품으로 구성하는 편이 좋아요."
            patched["reason_points"] = [
                "알레르기나 식이 제약은 소량 노출도 문제가 될 수 있어 계획에서 명확히 빼는 게 안전합니다.",
                "단백질, 칼슘, 지방 같은 영양 목표는 다른 식품으로 대체할 수 있어요.",
            ]
            patched["suggested_action"] = "식품 라벨을 확인하고, 반응 이력이 있으면 전문가 상담을 우선하세요."
        elif profile.get("medical_conditions") or profile.get("conditions"):
            patched["core_message"] = f"{target}가 있으면 무리한 강도나 증상을 악화시킬 수 있는 방식은 피하는 편이 안전해요."
            patched["reason_points"] = [
                "질환이나 복용약이 있으면 운동 강도와 식사 제한에 대한 반응이 달라질 수 있어요.",
                "증상이 있거나 조절 중인 상태라면 낮은 강도와 안정적인 식사 패턴부터 확인하는 게 안전합니다.",
            ]
            patched["suggested_action"] = "증상 변화가 있거나 약을 조절 중이면 전문가 확인을 우선하세요."
        else:
            patched["core_message"] = f"{target}가 있으면 통증을 키우는 고충격 동작과 깊은 가동범위 동작은 피하는 편이 안전해요."
            patched["reason_points"] = [
                "통증이 있는 부위에 반복 충격이나 비틀림이 들어가면 회복이 늦어질 수 있어요.",
                "대신 통증 없는 범위의 걷기, 가벼운 근력, 안정화 운동부터 확인하는 게 안전합니다.",
            ]
            patched["suggested_action"] = "통증이 생기면 즉시 중단하고, 지속되거나 붓기/불안정감이 있으면 전문가 상담을 권장해요."
        for note in _profile_safety_notes(profile, "workout"):
            _append_unique(patched["safety_notes"], note)
        if constraints:
            patched["search_grounding_summary"] = f"사용자 제약({', '.join(constraints)})을 기준으로 피해야 할 운동을 좁혔어요."
    else:
        profile_note = _profile_fit_note(profile)
        if profile_note:
            _append_unique(patched["reason_points"], profile_note)
        memory_note = _memory_grounding_note(state)
        if memory_note:
            _append_unique(patched["reason_points"], memory_note)
        for note in _profile_safety_notes(profile, None):
            _append_unique(patched["safety_notes"], note)

    memory_note = _memory_grounding_note(state)
    if memory_note:
        _append_unique(patched["reason_points"], memory_note)

    return patched


def _append_unique(items: list[str], value: str) -> None:
    text = value.strip()
    if text and text not in items:
        items.append(text)


def _profile_frequency(profile: dict) -> int | None:
    for key in (
        "exercise_frequency",
        "workout_frequency",
        "frequency_per_week",
        "weekly_workouts",
        "target_workouts_per_week",
        "preferred_workout_days",
    ):
        value = profile.get(key)
        if not value:
            continue
        if isinstance(value, (int, float)):
            count = int(value)
        elif isinstance(value, list):
            count = len(value)
        else:
            text = str(value).strip().lower()
            if any(marker in text for marker in ("daily", "every day", "매일")):
                count = 7
            elif "평일" in text:
                count = 5
            elif "주말" in text:
                count = 2
            else:
                match = re.search(r"([1-7])", text)
                if not match:
                    continue
                count = int(match.group(1))
        if 1 <= count <= 7:
            return count
    return None


def _profile_social_orientation(profile: dict) -> str | None:
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
    if re.fullmatch(r"[ei][ns][tf][jp]", mbti):
        return "extrovert" if mbti.startswith("e") else "introvert"
    return None


def _social_orientation_label(profile: dict) -> str:
    orientation = _profile_social_orientation(profile)
    if orientation == "extrovert":
        return "외향형"
    if orientation == "introvert":
        return "내향형"
    return ""


def _social_workout_note(profile: dict) -> str:
    orientation = _profile_social_orientation(profile)
    if orientation == "extrovert":
        return "외향형 성향이라 그룹 수업, 친구와 걷기, 함께 하는 챌린지 중 하나를 선택지로 둠"
    if orientation == "introvert":
        return "내향형 성향이라 혼자 조용히 할 수 있는 홈트, 고정 루틴, 이어폰 걷기 중심"
    return ""


def _profile_goal_text(profile: dict) -> str:
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


def _is_fat_loss_goal(profile: dict) -> bool:
    return any(
        marker in _profile_goal_text(profile)
        for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량", "체중 감량")
    )


def _is_muscle_goal(profile: dict) -> bool:
    return any(
        marker in _profile_goal_text(profile)
        for marker in ("muscle", "strength", "근육", "근력", "증량", "벌크")
    )


def _is_mobility_or_health_goal(profile: dict) -> bool:
    return any(
        marker in _profile_goal_text(profile)
        for marker in ("mobility", "health", "glucose", "혈당", "건강", "가동성")
    )


def _workout_category_balance_note(profile: dict) -> str:
    orientation = _profile_social_orientation(profile)
    if _is_fat_loss_goal(profile) and orientation == "introvert":
        return "다이어트/감량 목표와 내향형 성향을 함께 반영해 집에서 하는 유산소를 우선하고 스트레칭, 상체, 하체를 보조로 구성했어요."
    if _is_fat_loss_goal(profile):
        return "다이어트/감량 목표라 유산소를 우선하되 스트레칭, 상체, 하체를 모두 포함해 균형을 맞췄어요."
    if orientation == "introvert":
        return "내향형 성향을 반영해 스트레칭, 유산소, 상체, 하체를 혼자 하기 쉬운 홈트 중심으로 구성했어요."
    if orientation == "extrovert":
        return "외향형 성향을 반영해 스트레칭, 유산소, 상체, 하체에 함께 하기 좋은 선택지를 섞었어요."
    return "운동 구성을 스트레칭, 유산소, 상체, 하체 4종류로 나눠 균형을 맞췄어요."


def _workout_goal_note(profile: dict) -> str:
    goal_text = _profile_goal_text(profile)
    if any(marker in goal_text for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량", "체중 감량")):
        return "다이어트/감량 목표라 저충격 유산소와 전신 근력 조합"
    if any(marker in goal_text for marker in ("muscle", "strength", "근육", "근력", "증량", "벌크")):
        return "근력/근육 증가 목표라 큰 근육 위주로 점진적 과부하"
    if any(marker in goal_text for marker in ("endurance", "지구력", "러닝", "cardio")):
        return "지구력 목표라 유산소 시간을 천천히 늘리는 구성"
    if any(marker in goal_text for marker in ("mobility", "health", "glucose", "혈당", "건강", "가동성")):
        return "건강/가동성 목표라 관절 부담을 낮춘 가동성, 균형, 저강도 유산소 중심"
    if any(marker in goal_text for marker in ("consistency", "habit", "지속", "습관")):
        return "지속성 목표라 실패해도 이어갈 수 있는 낮은 기준"
    return ""


def _workout_frequency_note(frequency: int | None) -> str:
    if not frequency:
        return ""
    if frequency <= 2:
        return f"주 {frequency}회 기준으로 회복일을 충분히 남김"
    if frequency >= 5:
        return f"주 {frequency}회 기준이라 세션별 부담을 나눠 진행"
    return f"주 {frequency}회 루틴으로 반복 가능하게 구성"


def _profile_fit_note(profile: dict) -> str:
    parts: list[str] = []
    age = profile.get("age")
    if age:
        parts.append(f"{age}세")
    gender = profile.get("gender")
    if gender:
        parts.append(f"성별 {gender}")
    weight = _profile_weight(profile)
    if weight:
        parts.append(f"체중 {weight}kg")
    level = profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level")
    if level:
        parts.append(f"운동 수준 {level}")
    goal = profile.get("goal")
    if goal:
        parts.append(f"목표 {goal}")
    available = profile.get("available_time_minutes")
    if available:
        parts.append(f"가능 시간 {available}분")
    frequency = _profile_frequency(profile)
    if frequency:
        parts.append(f"운동 빈도 주 {frequency}회")
    social_label = _social_orientation_label(profile)
    if social_label:
        parts.append(f"운동 성향 {social_label}")
    lifestyle = profile.get("lifestyle") or profile.get("schedule")
    if lifestyle:
        parts.append(f"생활패턴 {lifestyle}")
    context_notes = _as_text_list(profile.get("context_notes"))
    if context_notes:
        parts.append(f"추가 맥락 {', '.join(context_notes)}")
    if not parts:
        return ""
    return "사용자 프로필(" + ", ".join(str(item) for item in parts) + ")에 맞춰 강도와 분량을 조정했어요."


def _empathy_note(profile: dict, state: GraphState) -> str:
    text = " ".join(
        str(value)
        for value in (
            profile.get("emotional_context"),
            (state.get("emotion") or {}).get("label") if state.get("emotion") else None,
        )
        if value
    ).lower()
    if not text or text == "normal":
        return ""
    if any(marker in text for marker in ("fail", "실패", "discouraged", "desperate", "burden", "burnout", "지쳐", "힘들", "불안", "걱정", "overwhelmed", "anxious", "worried", "stress", "body image")):
        return "못 한 게 문제가 아니라 다시 시작할 수 있게 부담을 줄이는 게 우선이에요."
    if "care" in text:
        return "지금은 의지를 더 짜내기보다 부담을 낮춰 다시 이어갈 수 있게 잡을게요."
    return ""


def _profile_safety_notes(profile: dict, proposed_plan_type: str | None) -> list[str]:
    notes: list[str] = []
    injuries = _as_text_list(profile.get("injury_history"))
    conditions = _as_text_list(profile.get("medical_conditions") or profile.get("conditions"))
    pain_points = _as_text_list(profile.get("pain_points"))
    allergies = _as_text_list(profile.get("allergies") or profile.get("dietary_restrictions"))
    context_notes = _as_text_list(profile.get("context_notes"))

    if injuries or pain_points:
        target = ", ".join([*injuries, *pain_points])
        notes.append(f"{target} 관련 통증이 생기면 즉시 중단하고 강도를 낮추세요.")
    if conditions:
        notes.append(f"질환 정보({', '.join(conditions)})가 있으므로 증상이 있거나 약을 복용 중이면 전문가 상담을 우선하세요.")
    if (proposed_plan_type == "diet" or (proposed_plan_type is None and allergies)) and allergies:
        notes.append(f"알레르기/식이 제약({', '.join(allergies)})은 제외하고 안전한 대체 식품으로 바꾸세요.")
    if context_notes:
        notes.append(f"추가 맥락({', '.join(context_notes)})을 반영해 무리한 방식은 피하세요.")
    goal = str(profile.get("goal") or "").lower()
    weight = _profile_weight(profile)
    age = _safe_int(profile.get("age"))
    fat_loss_goal = any(marker in goal for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량"))
    if "extreme" in goal or "급" in goal:
        notes.append("단기간에 큰 폭으로 감량하거나 굶는 방식은 피하고, 지속 가능한 감량 속도로 조정하세요.")
    if fat_loss_goal and ((age and age < 19) or (weight and weight <= 50)):
        notes.append("성장기이거나 낮은 체중에서의 감량 목표는 굶기는 피하고 균형 식사와 체력 유지 중심으로 조정하세요.")
    return notes


def _constraint_grounding_note(profile: dict, proposed_plan_type: str | None) -> str:
    injuries = _as_text_list(profile.get("injury_history"))
    conditions = _as_text_list(profile.get("medical_conditions") or profile.get("conditions"))
    pain_points = _as_text_list(profile.get("pain_points"))
    allergies = _as_text_list(profile.get("allergies") or profile.get("dietary_restrictions"))
    context_notes = _as_text_list(profile.get("context_notes"))
    constraints = [
        *injuries,
        *conditions,
        *pain_points,
        *allergies,
        *context_notes,
    ]
    if not constraints:
        return ""
    if proposed_plan_type == "diet" or (proposed_plan_type is None and allergies and not (injuries or pain_points)):
        label = "식단 제약"
    elif proposed_plan_type is None:
        label = "건강 제약"
    else:
        label = "운동 제약"
    return f"{label}({', '.join(constraints)})을 반영해 위험 요소를 낮췄어요."


def _is_pure_workout_plan(plan: list[dict]) -> bool:
    if not plan:
        return False
    meal_markers = {"breakfast", "lunch", "dinner", "snack", "아침", "점심", "저녁", "간식", "식단", "식사"}
    for item in plan:
        name = str(item.get("name") or "").strip().lower()
        detail = str(item.get("detail") or "").strip().lower()
        if any(marker in name or marker in detail for marker in meal_markers):
            return False
        if not item.get("ex_list"):
            return False
    return True


def _workout_category_sequence(profile: dict) -> tuple[str, ...]:
    if _is_fat_loss_goal(profile):
        return ("cardio", "lower_body", "upper_body", "stretching")
    if _is_mobility_or_health_goal(profile):
        return ("stretching", "cardio", "lower_body", "upper_body")
    if _is_muscle_goal(profile):
        return ("upper_body", "lower_body", "cardio", "stretching")
    return ("stretching", "cardio", "upper_body", "lower_body")


def _workout_item_category(item: dict) -> str | None:
    text_parts = [
        str(item.get("name") or ""),
        str(item.get("detail") or ""),
    ]
    for exercise in item.get("ex_list") or []:
        if isinstance(exercise, dict):
            text_parts.append(str(exercise.get("exercise_name") or ""))
    text = " ".join(text_parts).lower()
    if any(keyword.lower() in text for keyword in _WORKOUT_CATEGORY_KEYWORDS["stretching"]):
        return "stretching"
    scores: dict[str, int] = {}
    for category, keywords in _WORKOUT_CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for keyword in keywords if keyword.lower() in text)
    best_score = max(scores.values() or [0])
    if best_score <= 0:
        return None
    winners = [category for category, score in scores.items() if score == best_score]
    return winners[0] if len(winners) == 1 else None


def _tag_workout_category_item(item: dict, category: str) -> dict:
    next_item = dict(item)
    label = _WORKOUT_CATEGORY_LABELS[category]
    name = str(next_item.get("name") or label).strip()
    if label not in name:
        next_item["name"] = f"{label} - {name}"
    detail = str(next_item.get("detail") or "").strip()
    category_note = f"운동 종류: {label}"
    if category_note not in detail:
        next_item["detail"] = f"{detail} / {category_note}" if detail else category_note
    return next_item


def _adapt_existing_workout_category_item(
    item: dict,
    category: str,
    profile: dict,
    *,
    cap_sets: int,
    duration_cap: int | None,
    constraints: list[str],
    beginner: bool,
    advanced: bool,
    older: bool,
) -> dict:
    next_item = _tag_workout_category_item(item, category)
    if category == "cardio" and _profile_social_orientation(profile) == "introvert":
        ex_list_text = " ".join(
            str(exercise.get("exercise_name") or "")
            for exercise in next_item.get("ex_list") or []
            if isinstance(exercise, dict)
        ).lower()
        if not any(marker in ex_list_text for marker in ("집", "홈", "실내", "제자리")):
            next_item["ex_list"] = _category_exercises(
                category,
                profile,
                cap_sets=cap_sets,
                duration_cap=duration_cap,
                constraints=constraints,
                beginner=beginner,
                advanced=advanced,
                older=older,
            )
            detail = str(next_item.get("detail") or "").strip()
            indoor_note = "내향형 감량 목표에 맞춰 집에서 가능한 유산소로 보정"
            if indoor_note not in detail:
                next_item["detail"] = f"{detail} / {indoor_note}" if detail else indoor_note
    return next_item


def _ensure_workout_category_coverage(
    plan: list[dict],
    profile: dict,
    *,
    cap_sets: int,
    duration_cap: int | None,
    constraints: list[str],
    beginner: bool,
    advanced: bool,
    older: bool,
) -> list[dict]:
    if not _is_pure_workout_plan(plan):
        return plan

    first_day = str((plan[0] or {}).get("day") or "").strip() if plan else ""
    day = first_day or kst_today_iso()
    existing_by_category: dict[str, dict] = {}
    for item in plan:
        category = _workout_item_category(item)
        if category and category not in existing_by_category:
            existing_by_category[category] = _adapt_existing_workout_category_item(
                item,
                category,
                profile,
                cap_sets=cap_sets,
                duration_cap=duration_cap,
                constraints=constraints,
                beginner=beginner,
                advanced=advanced,
                older=older,
            )

    balanced: list[dict] = []
    for category in _workout_category_sequence(profile):
        if category in existing_by_category:
            balanced.append(existing_by_category[category])
            continue
        balanced.append(
            _build_profile_workout_category_item(
                category,
                profile,
                day=day,
                cap_sets=cap_sets,
                duration_cap=duration_cap,
                constraints=constraints,
                beginner=beginner,
                advanced=advanced,
                older=older,
            )
        )
    return balanced


def _build_profile_workout_category_item(
    category: str,
    profile: dict,
    *,
    day: str,
    cap_sets: int,
    duration_cap: int | None,
    constraints: list[str],
    beginner: bool,
    advanced: bool,
    older: bool,
) -> dict:
    label = _WORKOUT_CATEGORY_LABELS[category]
    orientation = _profile_social_orientation(profile)
    detail_parts = [f"{label} 축"]
    if category == "cardio" and _is_fat_loss_goal(profile):
        detail_parts.append("다이어트/감량 목표라 유산소 비중을 가장 크게 둠")
    elif category in {"upper_body", "lower_body"} and _is_fat_loss_goal(profile):
        detail_parts.append("감량 중 근손실 방지를 위한 보조 근력")
    elif category == "stretching":
        detail_parts.append("부상 예방과 회복을 위한 준비/마무리")
    if orientation == "introvert":
        detail_parts.append("내향형 성향에 맞춘 집에서 혼자 가능한 구성")
    elif orientation == "extrovert":
        detail_parts.append("외향형 성향에 맞춰 함께 하기 쉬운 선택지")
    available = profile.get("available_time_minutes")
    if available:
        detail_parts.append(f"가능 시간 {available}분 안에서 진행")
    frequency_note = _workout_frequency_note(_profile_frequency(profile))
    if frequency_note:
        detail_parts.append(frequency_note)
    if beginner or older:
        detail_parts.append("저강도")
    elif advanced:
        detail_parts.append("숙련자도 반복 가능한 기본 강도")
    if constraints:
        detail_parts.append(f"제약({', '.join(constraints)}) 고려")

    return {
        "name": f"{label} 루틴",
        "detail": " / ".join(dict.fromkeys(part for part in detail_parts if part)),
        "day": day,
        "ex_list": _category_exercises(
            category,
            profile,
            cap_sets=cap_sets,
            duration_cap=duration_cap,
            constraints=constraints,
            beginner=beginner,
            advanced=advanced,
            older=older,
        ),
    }


def _category_exercises(
    category: str,
    profile: dict,
    *,
    cap_sets: int,
    duration_cap: int | None,
    constraints: list[str],
    beginner: bool,
    advanced: bool,
    older: bool,
) -> list[dict]:
    orientation = _profile_social_orientation(profile)
    constraint_text = " ".join(constraints).lower()
    has_knee_or_ankle = any(marker in constraint_text for marker in ("무릎", "knee", "발목", "ankle"))
    has_back = any(marker in constraint_text for marker in ("허리", "back"))
    has_shoulder_or_wrist = any(marker in constraint_text for marker in ("어깨", "shoulder", "손목", "wrist"))

    sets = max(1, min(cap_sets, 2 if beginner or older or constraints else 3))
    if category == "cardio":
        duration = 25 if _is_fat_loss_goal(profile) else 18
        if advanced and _is_fat_loss_goal(profile):
            duration = 30
        if beginner or older:
            duration = min(duration, 15)
        if duration_cap:
            duration = min(duration, duration_cap)
        duration = max(8, duration)
        if has_knee_or_ankle or has_back:
            name = "실내 자전거" if orientation == "introvert" else "빠른 걷기"
        elif orientation == "introvert":
            name = "집에서 제자리 빠른 걷기"
        elif orientation == "extrovert":
            name = "친구와 빠른 걷기"
        else:
            name = "빠른 걷기"
        return [{"exercise_name": name, "duration_minutes": duration, "calories": duration * 6}]

    if category == "stretching":
        name = "고양이-소 스트레칭" if has_back else "전신 스트레칭"
        return [{"exercise_name": name, "sets": min(sets, 2), "calories": 40}]

    if category == "upper_body":
        if has_shoulder_or_wrist or beginner or older:
            names = ["월 푸시업", "밴드 로우"]
        elif advanced and _is_muscle_goal(profile):
            names = ["푸시업", "덤벨 로우"]
        elif orientation == "introvert":
            names = ["홈트 월 푸시업", "밴드 로우"]
        else:
            names = ["푸시업", "밴드 로우"]
        return [{"exercise_name": name, "sets": sets, "calories": 55} for name in names]

    if category == "lower_body":
        if has_knee_or_ankle or beginner or older:
            names = ["의자 스쿼트", "글루트 브릿지"]
        elif advanced and _is_muscle_goal(profile):
            names = ["스쿼트", "런지"]
        elif orientation == "introvert":
            names = ["홈트 의자 스쿼트", "글루트 브릿지"]
        else:
            names = ["스쿼트", "글루트 브릿지"]
        return [{"exercise_name": name, "sets": sets, "calories": 60} for name in names]

    return []


def _adjust_workout_plan_for_profile(plan: list[dict], profile: dict) -> list[dict]:
    if not plan:
        return plan
    available = _safe_int(profile.get("available_time_minutes"))
    level = str(profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level") or "").lower()
    beginner = any(marker in level for marker in ("beginner", "초보", "low", "낮"))
    advanced = any(marker in level for marker in ("advanced", "숙련", "상급", "고급"))
    intermediate = any(marker in level for marker in ("intermediate", "중급"))
    older = (_safe_int(profile.get("age")) or 0) >= 65
    frequency = _profile_frequency(profile)
    constraints = [
        *_as_text_list(profile.get("injury_history")),
        *_as_text_list(profile.get("pain_points")),
        *_as_text_list(profile.get("medical_conditions") or profile.get("conditions")),
    ]
    if beginner or older or constraints:
        cap_sets = 2
    elif advanced:
        cap_sets = 4
    else:
        cap_sets = 3
    if frequency and frequency <= 2 and not advanced:
        cap_sets = min(cap_sets, 2)
    duration_cap = max(8, min(20, available or 20)) if beginner or older or constraints or (available and available <= 20) else None
    if duration_cap is None and frequency and frequency <= 2 and available:
        duration_cap = max(12, min(35, available))
    goal_note = _workout_goal_note(profile)
    frequency_note = _workout_frequency_note(frequency)
    social_note = _social_workout_note(profile)
    weight_note = _weight_workout_note(profile)

    adjusted: list[dict] = []
    for item in plan:
        next_item = dict(item)
        detail = str(next_item.get("detail") or "").strip()
        detail_parts = [detail] if detail else []
        if beginner:
            detail_parts.append("초보자 기준 저강도")
        elif advanced:
            detail_parts.append("숙련자 기준으로 강도는 유지하되 회복 상태 확인")
        elif intermediate:
            detail_parts.append("중급자 기준 기본 볼륨")
        if available:
            detail_parts.append(f"가능 시간 {available}분 안에서 진행")
        if frequency_note:
            detail_parts.append(frequency_note)
        if goal_note:
            detail_parts.append(goal_note)
        if social_note:
            detail_parts.append(social_note)
        if weight_note:
            detail_parts.append(weight_note)
        if constraints:
            detail_parts.append(f"제약({', '.join(constraints)}) 고려")
        next_item["detail"] = " / ".join(dict.fromkeys(part for part in detail_parts if part))

        ex_list = []
        for exercise in next_item.get("ex_list") or []:
            next_exercise = dict(exercise)
            sets = next_exercise.get("sets")
            if isinstance(sets, int) and sets > cap_sets:
                next_exercise["sets"] = cap_sets
            duration = next_exercise.get("duration_minutes")
            if duration_cap and isinstance(duration, int) and duration > duration_cap:
                next_exercise["duration_minutes"] = duration_cap
            ex_list.append(next_exercise)
        next_item["ex_list"] = ex_list
        adjusted.append(next_item)
    return _ensure_workout_category_coverage(
        adjusted,
        profile,
        cap_sets=cap_sets,
        duration_cap=duration_cap,
        constraints=constraints,
        beginner=beginner,
        advanced=advanced,
        older=older,
    )


def _adjust_diet_plan_for_profile(plan: list[dict], profile: dict) -> list[dict]:
    if not plan:
        return plan
    allergies = _as_text_list(profile.get("allergies") or profile.get("dietary_restrictions"))
    adjusted: list[dict] = []
    for item in plan:
        next_item = dict(item)
        detail = _sanitize_diet_detail_for_profile(str(next_item.get("detail") or "").strip(), allergies)
        detail = _adapt_diet_detail_for_profile(detail, profile)
        detail = _sanitize_diet_detail_for_profile(detail, allergies)
        next_item["detail"] = _strip_plan_detail_explanations(detail)
        adjusted.append(next_item)
    return adjusted


def _has_mixed_workout_diet_items(plan: list[dict]) -> bool:
    has_workout = False
    has_diet = False
    for item in plan:
        if _is_diet_plan_item(item):
            has_diet = True
        elif isinstance(item, dict) and item.get("ex_list"):
            has_workout = True
    return has_workout and has_diet


def _is_diet_plan_item(item: dict) -> bool:
    text = f"{item.get('name') or ''} {item.get('detail') or ''}".lower()
    return any(
        marker in text
        for marker in (
            "breakfast",
            "lunch",
            "dinner",
            "snack",
            "meal",
            "아침",
            "점심",
            "저녁",
            "간식",
            "식단",
            "식사",
        )
    )


def _adjust_mixed_plan_for_profile(plan: list[dict], profile: dict) -> list[dict]:
    adjusted: list[dict] = []
    for item in plan:
        next_item = dict(item)
        if _is_diet_plan_item(next_item):
            next_item["ex_list"] = []
            adjusted.extend(_adjust_diet_plan_for_profile([next_item], profile))
            continue
        adjusted.append(_adjust_single_workout_item_for_profile(next_item, profile))
    return adjusted


def _adjust_single_workout_item_for_profile(item: dict, profile: dict) -> dict:
    adjusted = dict(item)
    available = _safe_int(profile.get("available_time_minutes"))
    level = str(profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level") or "").lower()
    beginner = any(marker in level for marker in ("beginner", "초보", "low", "낮"))
    advanced = any(marker in level for marker in ("advanced", "숙련", "상급", "고급"))
    older = (_safe_int(profile.get("age")) or 0) >= 65
    constraints = [
        *_as_text_list(profile.get("injury_history")),
        *_as_text_list(profile.get("pain_points")),
        *_as_text_list(profile.get("medical_conditions") or profile.get("conditions")),
    ]
    cap_sets = 2 if beginner or older or constraints else 4 if advanced else 3
    duration_cap = max(5, min(20, available or 20)) if beginner or older or constraints or (available and available <= 20) else None

    detail = str(adjusted.get("detail") or "").strip()
    detail_parts = [detail] if detail else []
    if beginner:
        detail_parts.append("초보자 기준 저강도")
    if available:
        detail_parts.append(f"가능 시간 {available}분 안에서 진행")
    if constraints:
        detail_parts.append(f"제약({', '.join(constraints)}) 고려")
    adjusted["detail"] = " / ".join(dict.fromkeys(part for part in detail_parts if part))

    ex_list = []
    for exercise in adjusted.get("ex_list") or []:
        next_exercise = dict(exercise)
        sets = next_exercise.get("sets")
        if isinstance(sets, int) and sets > cap_sets:
            next_exercise["sets"] = cap_sets
        duration = next_exercise.get("duration_minutes")
        if duration_cap and isinstance(duration, int) and duration > duration_cap:
            next_exercise["duration_minutes"] = duration_cap
        ex_list.append(next_exercise)
    adjusted["ex_list"] = ex_list
    return adjusted


def _sanitize_diet_detail_for_profile(detail: str, allergies: list[str]) -> str:
    if not detail or not allergies:
        return detail
    allergy_text = " ".join(allergies).lower()
    next_detail = detail
    if any(marker in allergy_text for marker in ("우유", "유당", "dairy", "milk")):
        next_detail = re.sub(
            r"그릭요거트|요거트|우유|유제품|greek\s+yogurts?|greek\s+yoghurts?|yogurts?|yoghurts?|milk|dairy",
            "무가당 콩요거트",
            next_detail,
            flags=re.IGNORECASE,
        )
    if any(marker in allergy_text for marker in ("견과", "땅콩", "캐슈", "nut", "peanut", "cashew")):
        next_detail = re.sub(
            r"견과류|땅콩버터|땅콩|캐슈넛|캐슈|nuts?|peanuts?|peanut\s+butter|cashews?",
            "오트",
            next_detail,
            flags=re.IGNORECASE,
        )
    if any(marker in allergy_text for marker in ("계란", "egg")):
        next_detail = re.sub(r"계란|달걀|egg", "두부", next_detail, flags=re.IGNORECASE)
    if any(marker in allergy_text for marker in ("대두", "콩 알레르기", "콩알레르기", "콩 못", "콩못", "soy")):
        next_detail = re.sub(r"두부\s*스테이크|두부|대두|콩요거트|두유|soy", "렌틸콩볼", next_detail, flags=re.IGNORECASE)
    if any(marker in allergy_text for marker in ("밀", "글루텐", "wheat", "gluten")):
        next_detail = re.sub(r"통곡물빵|빵|파스타|밀|wheat|gluten", "고구마", next_detail, flags=re.IGNORECASE)
    if any(marker in allergy_text for marker in ("갑각류", "새우", "shellfish", "shrimp")):
        shellfish_replacement = "렌틸콩볼" if any(marker in allergy_text for marker in ("대두", "콩 알레르기", "콩알레르기", "soy")) else "두부"
        next_detail = re.sub(r"새우|갑각류|shrimp|shellfish", shellfish_replacement, next_detail, flags=re.IGNORECASE)
    if any(marker in allergy_text for marker in ("양파", "onion")):
        next_detail = re.sub(r"양파|onion", "저자극 채소", next_detail, flags=re.IGNORECASE)
    return next_detail


def _adapt_diet_detail_for_profile(detail: str, profile: dict) -> str:
    if not detail:
        return detail

    next_detail = detail
    if _is_plant_based_diet(profile):
        replacements = (
            (r"chicken\s+breast|chicken|닭가슴살|닭고기", "두부 스테이크"),
            (r"salmon|fish|연어|생선", "렌틸콩"),
            (r"greek\s+yogurts?|greek\s+yoghurts?|yogurts?|yoghurts?|milk|dairy|그릭요거트|요거트|우유|유제품", "무가당 콩요거트"),
            (r"계란|달걀|egg", "두부"),
            (r"beef|pork|meat|소고기|돼지고기|고기", "콩 단백질"),
        )
        for pattern, replacement in replacements:
            next_detail = re.sub(pattern, replacement, next_detail, flags=re.IGNORECASE)

    goal_text = _profile_goal_text(profile)
    condition_text = " ".join(
        str(value)
        for value in (
            profile.get("medical_conditions"),
            profile.get("conditions"),
            profile.get("diet_goal"),
            profile.get("primary_goal"),
        )
        if value
    ).lower()
    if any(marker in f"{goal_text} {condition_text}" for marker in ("glucose", "blood sugar", "diabetes", "당뇨", "혈당")):
        next_detail = re.sub(r"\bfruit\b|과일", "블루베리", next_detail, flags=re.IGNORECASE)
        next_detail = re.sub(r"white rice|흰쌀밥|쌀밥", "현미밥", next_detail, flags=re.IGNORECASE)

    if any(marker in f"{goal_text} {condition_text}" for marker in ("hypertension", "blood pressure", "고혈압", "혈압")):
        next_detail = re.sub(r"ramen|라면|햄|소시지|소세지", "현미밥과 데친 채소", next_detail, flags=re.IGNORECASE)

    if any(marker in goal_text for marker in ("muscle", "strength", "근육", "근력", "증량")):
        lower = next_detail.lower()
        if not any(marker in lower for marker in ("chicken", "두부", "콩", "렌틸", "salmon", "egg", "단백")):
            allergy_text = " ".join(_as_text_list(profile.get("allergies") or profile.get("dietary_restrictions"))).lower()
            protein_boost = "렌틸콩볼" if any(marker in allergy_text for marker in ("대두", "콩 알레르기", "콩알레르기", "콩 못", "콩못", "soy")) else "두부"
            next_detail = f"{next_detail} + {protein_boost}"

    return next_detail


def _is_plant_based_diet(profile: dict) -> bool:
    text = " ".join(
        str(value)
        for value in (
            profile.get("diet_type"),
            profile.get("diet_goal"),
            profile.get("primary_goal"),
            profile.get("lifestyle"),
            profile.get("schedule"),
            " ".join(_as_text_list(profile.get("context_notes"))),
        )
        if value
    ).lower()
    return any(marker in text for marker in ("vegan", "vegetarian", "plant based", "plant-based", "비건", "채식"))


def _profile_weight(profile: dict) -> int | None:
    for key in ("weight", "body_weight", "body_weight_kg", "current_weight_kg"):
        value = profile.get(key)
        if value:
            return _safe_int(value)
    return None


def _weight_workout_note(profile: dict) -> str:
    weight = _profile_weight(profile)
    if not weight:
        return ""
    goal_text = " ".join(
        str(value)
        for value in (profile.get("goal"), profile.get("diet_goal"), profile.get("primary_goal"))
        if value
    ).lower()
    if weight >= 90:
        return "체중 부담을 고려해 점프보다 저충격 유산소와 안정적인 근력 중심"
    if weight <= 50 and any(marker in goal_text for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량")):
        return "낮은 체중과 감량 목표가 함께 있어 체력 유지와 근손실 방지 중심"
    return ""


def _diet_goal_note(profile: dict) -> str:
    goal_text = " ".join(
        str(value)
        for value in (profile.get("goal"), profile.get("diet_goal"), profile.get("diet_type"), profile.get("primary_goal"))
        if value
    ).lower()
    if any(marker in goal_text for marker in ("glucose", "blood sugar", "혈당", "diabetes", "당뇨")):
        return "혈당 안정 목표 고려"
    if any(marker in goal_text for marker in ("muscle", "strength", "근육", "근력", "증량")):
        return "근육 증가 목표에 맞춰 단백질 포함"
    if any(marker in goal_text for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량")):
        return "굶지 않는 감량 목표 고려"
    return ""


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
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                return int(float(match.group(0)))
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _plan_contract_needs_fallback(
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
) -> bool:
    if proposed_plan_type not in {"workout", "diet"}:
        return bool(proposed_plan)
    if not proposed_plan:
        return False

    for item in proposed_plan:
        if not isinstance(item, dict):
            return True
        if not str(item.get("name") or "").strip():
            return True
        if not _is_iso_day_string(item.get("day")):
            return True

        if proposed_plan_type == "workout":
            exercises = item.get("ex_list") or []
            if not isinstance(exercises, list) or not exercises:
                return True
            for exercise in exercises:
                if not isinstance(exercise, dict):
                    return True
                if not str(exercise.get("exercise_name") or "").strip():
                    return True
                for numeric_key in ("sets", "duration_minutes", "calories"):
                    value = exercise.get(numeric_key)
                    if value in (None, ""):
                        continue
                    parsed = _safe_int(value)
                    if parsed is None or parsed < 0:
                        return True

        if proposed_plan_type == "diet":
            if item.get("ex_list"):
                return True
            if not str(item.get("detail") or "").strip():
                return True

    return False


def _diet_plan_requires_safe_fallback(
    proposed_plan: list[dict],
    profile: dict,
    request_text: object = "",
) -> bool:
    if not proposed_plan:
        return False

    profile_parts: list[str] = []
    for key in ("diet_type", "dietary_restrictions", "allergies", "allergy"):
        profile_parts.extend(_as_text_list(profile.get(key)))
    profile_parts.append(str(request_text or ""))
    profile_text = " ".join(profile_parts).lower()
    plan_text = _plan_text_for_safety_check(proposed_plan)

    vegetarian = any(token in profile_text for token in ("vegetarian", "vegan", "채식", "비건"))
    dairy_free = any(token in profile_text for token in ("dairy", "milk", "유제품", "우유"))
    soy_free = any(token in profile_text for token in ("soy", "대두", "콩 알레르기", "콩알레르기", "콩 못", "콩못"))
    egg_free = any(token in profile_text for token in ("egg", "계란", "달걀"))
    nut_free = any(token in profile_text for token in ("nut", "peanut", "견과", "땅콩"))
    fish_free = any(token in profile_text for token in ("fish", "seafood", "생선", "해산물", "갑각류", "새우"))
    hypertension = any(token in profile_text for token in ("hypertension", "고혈압", "혈압"))
    diabetes = any(token in profile_text for token in ("diabetes", "glucose", "당뇨", "혈당"))
    kidney = any(token in profile_text for token in ("kidney", "renal", "ckd", "신장", "콩팥", "만성신부전"))
    gout = any(token in profile_text for token in ("gout", "uric acid", "통풍", "요산"))
    pregnancy = any(token in profile_text for token in ("pregnancy", "pregnant", "임신", "임산부"))
    eating_risk = any(token in profile_text for token in ("eating disorder", "섭식", "폭식", "절식"))

    if vegetarian and any(term in plan_text for term in _DIET_MEAT_CONFLICT_TERMS):
        return True

    if dairy_free:
        scrubbed = plan_text
        for replacement in _DIET_DAIRY_ALLOWED_REPLACEMENTS:
            scrubbed = scrubbed.replace(replacement, "")
        if any(term in scrubbed for term in _DIET_DAIRY_CONFLICT_TERMS):
            return True
    if soy_free and any(term in plan_text for term in _DIET_SOY_CONFLICT_TERMS):
        return True
    if egg_free and any(term in plan_text for term in _DIET_EGG_CONFLICT_TERMS):
        return True
    if nut_free and any(term in plan_text for term in _DIET_NUT_CONFLICT_TERMS):
        return True
    if fish_free and any(term in plan_text for term in _DIET_FISH_CONFLICT_TERMS):
        return True
    if hypertension and any(term in plan_text for term in _DIET_SODIUM_CONFLICT_TERMS):
        return True
    if diabetes and any(term in plan_text for term in _DIET_SUGAR_CONFLICT_TERMS):
        return True
    if kidney and any(term in plan_text for term in _DIET_KIDNEY_CONFLICT_TERMS):
        return True
    if gout and any(term in plan_text for term in _DIET_GOUT_CONFLICT_TERMS):
        return True
    if pregnancy and any(term in plan_text for term in _DIET_PREGNANCY_CONFLICT_TERMS):
        return True
    if eating_risk and any(term in plan_text for term in _DIET_EATING_RISK_CONFLICT_TERMS):
        return True

    return False


def _plan_text_for_safety_check(proposed_plan: list[dict]) -> str:
    parts: list[str] = []
    for item in proposed_plan or []:
        if not isinstance(item, dict):
            continue
        parts.extend(str(item.get(key) or "") for key in ("name", "detail"))
        for exercise in item.get("ex_list") or []:
            if isinstance(exercise, dict):
                parts.append(str(exercise.get("exercise_name") or ""))
    return " ".join(parts).lower()


_DIET_MEAT_CONFLICT_TERMS = (
    "닭",
    "닭가슴살",
    "소고기",
    "돼지고기",
    "연어",
    "참치",
    "생선",
    "새우",
    "crab",
    "shrimp",
    "chicken",
    "beef",
    "pork",
    "salmon",
    "tuna",
    "fish",
)
_DIET_DAIRY_CONFLICT_TERMS = (
    "우유",
    "치즈",
    "요거트",
    "요구르트",
    "버터",
    "크림",
    "milk",
    "cheese",
    "yogurt",
    "butter",
    "cream",
)
_DIET_SOY_CONFLICT_TERMS = ("두부", "두유", "대두", "콩요거트", "템페", "soy", "soybean")
_DIET_EGG_CONFLICT_TERMS = ("계란", "달걀", "egg")
_DIET_NUT_CONFLICT_TERMS = ("견과", "땅콩", "아몬드", "호두", "peanut", "almond", "walnut", "nut")
_DIET_FISH_CONFLICT_TERMS = ("생선", "연어", "참치", "새우", "갑각류", "fish", "salmon", "tuna", "shrimp", "shellfish")
_DIET_SODIUM_CONFLICT_TERMS = ("라면", "햄", "소시지", "베이컨", "젓갈", "국물", "짠", "나트륨")
_DIET_SUGAR_CONFLICT_TERMS = ("설탕", "시럽", "탄산", "주스", "케이크", "과자", "디저트")
_DIET_KIDNEY_CONFLICT_TERMS = ("고단백", "프로틴", "단백질 쉐이크", "크레아틴", "high protein", "protein shake")
_DIET_GOUT_CONFLICT_TERMS = ("내장", "곱창", "멸치", "정어리", "맥주", "조개", "새우", "purine", "beer")
_DIET_PREGNANCY_CONFLICT_TERMS = ("생선회", "회", "날달걀", "알코올", "술", "와인", "맥주", "raw fish", "raw egg", "alcohol")
_DIET_EATING_RISK_CONFLICT_TERMS = ("900kcal", "800kcal", "단식", "굶", "하루 한 끼", "원푸드", "절식", "fasting")
_DIET_DAIRY_ALLOWED_REPLACEMENTS = (
    "콩요거트",
    "코코넛요거트",
    "무가당 콩요거트",
    "두유",
    "아몬드유",
    "오트밀크",
    "귀리우유",
    "비건 요거트",
    "soy yogurt",
    "coconut yogurt",
    "soy milk",
    "almond milk",
    "oat milk",
    "non-dairy",
    "dairy-free",
)


def _is_iso_day_string(value: object) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()))


def _render_plan_preview(draft_result: DraftResponse, state: GraphState) -> str:
    if state.get("intent") not in {INTENT_PLAN, INTENT_MODIFY}:
        return ""

    plan_items = [item.model_dump() for item in draft_result.proposed_plan] if draft_result.proposed_plan else []
    if not plan_items:
        return ""

    visible_limit = _plan_preview_visible_limit(plan_items)
    lines: list[str] = []
    for item in plan_items[:visible_limit]:
        title = _plan_item_title(item)
        detail = _plan_item_detail(item)
        if detail:
            lines.append(f"- {title}: {detail}")
        else:
            lines.append(f"- {title}")

    remaining = len(plan_items) - visible_limit
    if remaining > 0:
        lines.append(f"- 외 {remaining}개 세부 항목")

    return "\n".join(lines)


def _render_plan_preview_from_items(plan_items: list[dict]) -> str:
    if not plan_items:
        return ""

    visible_limit = _plan_preview_visible_limit(plan_items)
    lines: list[str] = []
    for item in plan_items[:visible_limit]:
        title = _plan_item_title(item)
        detail = _plan_item_detail(item)
        lines.append(f"- {title}: {detail}" if detail else f"- {title}")
    remaining = len(plan_items) - visible_limit
    if remaining > 0:
        lines.append(f"- 외 {remaining}개 세부 항목")
    return "\n".join(lines)


def _plan_preview_visible_limit(plan_items: list[dict]) -> int:
    if len(plan_items) <= 7:
        return len(plan_items)
    return 4


def _plan_item_title(item: dict) -> str:
    day = str(item.get("day") or "").strip()
    name = str(item.get("name") or "").strip() or "계획 항목"
    return f"{day} {name}".strip()


def _plan_item_detail(item: dict) -> str:
    detail = _strip_plan_detail_explanations(str(item.get("detail") or "").strip())
    exercise_lines = _exercise_preview(item.get("ex_list") or [])
    if exercise_lines:
        return exercise_lines
    return detail


_PLAN_DETAIL_EXPLANATION_MARKERS = (
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
    "가능 시간",
    "성향",
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
_PLAN_DETAIL_SPLIT_RE = re.compile(r"\s*(?:/|;|\||\n|•|·|\s+-\s+|(?<=[.!?。])\s+)\s*")
_PLAN_DETAIL_BRACKET_RE = re.compile(
    r"\s*[\(\[][^\)\]]*(?:"
    + "|".join(re.escape(marker) for marker in _PLAN_DETAIL_EXPLANATION_MARKERS)
    + r")[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_PLAN_RATIONALE_PREFIX_MARKERS = (
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


def _strip_plan_detail_explanations(detail: str) -> str:
    if not detail:
        return ""

    detail = re.sub(r"\s+", " ", _PLAN_DETAIL_BRACKET_RE.sub("", detail)).strip()
    pieces = [
        piece.strip()
        for piece in _PLAN_DETAIL_SPLIT_RE.split(detail)
        if piece.strip()
    ]
    concrete = [_clean_plan_detail_piece(piece) for piece in pieces]
    concrete = [piece for piece in concrete if piece and not _is_explanatory_plan_piece(piece)]
    if not concrete and pieces:
        concrete = [_clean_plan_detail_piece(pieces[0])]
    return _bound_plan_detail(" / ".join(piece for piece in concrete if piece).strip())


def _clean_plan_detail_piece(piece: str) -> str:
    text = _PLAN_DETAIL_BRACKET_RE.sub("", str(piece or "")).strip()
    if not text:
        return ""

    lowered = text.lower()
    marker_positions = [
        lowered.find(marker.lower())
        for marker in _PLAN_DETAIL_EXPLANATION_MARKERS
        if marker.lower() in lowered
    ]
    if marker_positions:
        marker_index = min(position for position in marker_positions if position >= 0)
        if marker_index <= 0:
            return ""
        prefix = text[:marker_index].rstrip(" -:,.()[]")
        if _looks_like_plan_rationale_prefix(prefix):
            return ""
        text = prefix

    text = re.sub(r"\s*(?:때문에|위해서|위해|맞춰|반영해|반영하여).*$", "", text).strip()
    return text.strip(" -:,.")


def _looks_like_plan_rationale_prefix(prefix: str) -> bool:
    text = str(prefix or "").strip().lower()
    if not text:
        return True
    if len(text) <= 12 and any(marker in text for marker in _PLAN_RATIONALE_PREFIX_MARKERS):
        return True
    return False


def _is_explanatory_plan_piece(piece: str) -> bool:
    lowered = str(piece or "").lower()
    return any(marker.lower() in lowered for marker in _PLAN_DETAIL_EXPLANATION_MARKERS)


def _bound_plan_detail(detail: str) -> str:
    text = re.sub(r"\s+", " ", str(detail or "")).strip()
    if len(text) <= 96:
        return text

    parts = [
        part.strip()
        for part in re.split(r"\s*(?:,|/|\+|와|과)\s*", text)
        if part.strip()
    ]
    if len(parts) >= 2:
        text = ", ".join(parts[:3]).strip()
    return text[:96].rstrip(" ,/+")


def _exercise_preview(ex_list: list[dict]) -> str:
    if not isinstance(ex_list, list) or not ex_list:
        return ""

    parts: list[str] = []
    for exercise in ex_list[:3]:
        exercise_name = str(exercise.get("exercise_name") or "").strip()
        if not exercise_name:
            continue

        sets = exercise.get("sets")
        duration = exercise.get("duration_minutes")
        if isinstance(sets, int) and sets > 0:
            parts.append(f"{exercise_name} {sets}세트")
        elif isinstance(duration, int) and duration > 0:
            parts.append(f"{exercise_name} {duration}분")
        else:
            parts.append(exercise_name)

    remaining = len(ex_list) - 3
    if remaining > 0:
        parts.append(f"외 {remaining}종목")

    return ", ".join(parts)


def _normalize_plan_approval_question(
    components: DraftComponents,
    proposed_plan_type: str | None,
    proposed_plan_action: str | None,
) -> DraftComponents:
    patched = normalize_draft_components(dict(components))
    plan_label = "식단" if proposed_plan_type == "diet" else "운동"
    action_label = "수정할까요" if proposed_plan_action == "update" else "작성할까요"
    patched["approval_question"] = f"이 {plan_label} 플랜으로 {action_label}?"
    return patched


def _normalize_plan_core_message(
    components: DraftComponents,
    proposed_plan_type: str | None,
    proposed_plan_action: str | None,
) -> DraftComponents:
    patched = normalize_draft_components(dict(components))
    plan_label = "식단" if proposed_plan_type == "diet" else "운동"
    action_label = "수정했어요" if proposed_plan_action == "update" else "제안해요"
    patched["core_message"] = f"{plan_label} 플랜을 {action_label}."
    return patched


def _minimize_plan_exposition(
    components: DraftComponents,
    proposed_plan_type: str | None,
) -> DraftComponents:
    patched = normalize_draft_components(dict(components))
    patched["reason_points"] = []
    patched["suggested_action"] = ""
    patched["search_grounding_summary"] = ""
    patched["safety_notes"] = [
        note
        for note in patched["safety_notes"]
        if _is_essential_plan_safety_note(note, proposed_plan_type)
    ]
    return patched


def _is_essential_plan_safety_note(note: str, proposed_plan_type: str | None) -> bool:
    text = note.strip()
    if not text:
        return False
    if proposed_plan_type == "diet" and ("알레르기" in text or "식이 제약" in text):
        return False
    return any(
        marker in text
        for marker in (
            "즉시 중단",
            "통증",
            "119",
            "응급",
            "단기간",
            "굶",
            "성장기",
            "낮은 체중",
        )
    )


def _has_explicit_workout_domain(message: str) -> bool:
    lowered = message.lower()
    return any(keyword in lowered for keyword in ("운동", "러닝", "헬스", "근력", "유산소", "스트레칭", "산책", "웨이트", "workout", "exercise"))


def _has_explicit_diet_domain(message: str) -> bool:
    lowered = message.lower()
    return any(keyword in lowered for keyword in ("식단", "식사", "메뉴", "아침", "점심", "저녁", "meal", "diet"))


def _is_mixed_plan_type_request(message: str) -> bool:
    lowered = message.lower()
    has_plan_request = any(keyword in lowered for keyword in ("플랜", "계획", "작성", "추천", "짜줘", "세워", "잡아", "만들어", "루틴"))
    if not (has_plan_request and _has_explicit_workout_domain(lowered) and _has_explicit_diet_domain(lowered)):
        return False
    if any(marker in lowered for marker in ("같이", "함께", "둘 다", "둘다", "both", "운동과 식단", "운동 및 식단", "운동 계획과 식단")):
        return False
    return True


def _build_mixed_plan_clarification_draft() -> dict:
    components = normalize_draft_components(
        {
            "core_message": "운동 플랜과 식단 플랜은 따로 작성할게요. 먼저 하나를 골라주세요.",
            "reason_points": [],
            "suggested_action": "",
            "approval_question": None,
            "search_grounding_summary": "",
            "proposed_plan": [],
        }
    )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": [],
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "awaiting_plan_confirmation": False,
        "needs_clarification": True,
        "force_regenerate": False,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _build_starter_plan_fallback(
    state: GraphState,
) -> tuple[DraftComponents, str, list[dict], str | None, str | None]:
    plan_type = _infer_plan_type_from_message(str(state.get("user_message") or "")) or (
        "diet" if state.get("domain") == "diet" else "workout"
    )
    today = kst_today_iso()
    profile = _effective_user_profile(state)

    if plan_type == "diet":
        proposed_plan = [
            {
                "name": "아침",
                "detail": "현미죽, 블루베리, 삶은 달걀",
                "day": today,
                "ex_list": [],
            },
            {
                "name": "점심",
                "detail": "현미밥, 두부 스테이크, 구운 채소",
                "day": today,
                "ex_list": [],
            },
            {
                "name": "저녁",
                "detail": "렌틸콩 수프, 고구마, 데친 채소",
                "day": today,
                "ex_list": [],
            },
        ]
        components = normalize_draft_components(
            {
                "core_message": "기본 식단안을 제안할게요.",
                "reason_points": [],
                "suggested_action": "",
                "approval_question": "이 식단 플랜으로 작성할까요?",
                "search_grounding_summary": "",
            }
        )
    else:
        proposed_plan = [
            {
                "name": "전신 가벼운 근력 운동",
                "detail": "초보자도 시작하기 쉬운 저강도 전신 루틴",
                "day": today,
                "ex_list": [
                    {"exercise_name": "스쿼트", "sets": 2, "calories": 60},
                    {"exercise_name": "푸쉬업", "sets": 2, "calories": 50},
                    {"exercise_name": "버드독", "sets": 2, "calories": 30},
                ],
            },
            {
                "name": "가벼운 유산소",
                "detail": "호흡과 리듬을 살리는 걷기 중심 루틴",
                "day": today,
                "ex_list": [
                    {"exercise_name": "빠른 걷기", "duration_minutes": 20, "calories": 90},
                ],
            },
        ]
        components = normalize_draft_components(
            {
                "core_message": "가벼운 운동 계획을 제안할게요.",
                "reason_points": [],
                "suggested_action": "",
                "approval_question": "이 운동 플랜으로 작성할까요?",
                "search_grounding_summary": "",
            }
        )

    proposed_plan = _expand_long_range_plan_if_requested(state, proposed_plan, plan_type)
    components["plan_preview"] = _render_plan_preview_from_items(proposed_plan)
    components = _normalize_plan_approval_question(components, plan_type, "create")
    components = _minimize_plan_exposition(components, plan_type)
    draft_text = render_draft_preview(components)
    return components, draft_text, proposed_plan, plan_type, "create"


def _build_modify_plan_fallback(
    state: GraphState,
) -> tuple[DraftComponents, str, list[dict], str | None, str | None]:
    active_proposal = state.get("active_proposal") or {}
    plan_type = (
        state.get("modify_target")
        if state.get("modify_target") in {"workout", "diet"}
        else active_proposal.get("domain")
        if active_proposal.get("domain") in {"workout", "diet"}
        else state.get("proposed_plan_type")
        if state.get("proposed_plan_type") in {"workout", "diet"}
        else _infer_plan_type_from_message(str(state.get("user_message") or ""))
    )

    base_plan = state.get("proposed_plan") or active_proposal.get("items") or []
    proposed_plan = [dict(item) for item in base_plan if isinstance(item, dict)]
    if not proposed_plan or plan_type not in {"workout", "diet"}:
        if plan_type in {"workout", "diet"}:
            starter_state = {**state, "domain": plan_type}
            return _build_starter_plan_fallback(starter_state)
        components = normalize_draft_components(
            {
                "core_message": "수정할 플랜 항목을 확인하지 못했어요.",
                "reason_points": [],
                "suggested_action": "방금 제안한 플랜을 다시 보내주시면 그 범위에 맞춰 수정할게요.",
                "approval_question": None,
                "search_grounding_summary": "",
                "proposed_plan": [],
            }
        )
        return components, render_draft_preview(components), [], None, None

    components = normalize_draft_components(
        {
            "core_message": "요청한 범위에 맞춰 플랜을 수정했어요.",
            "reason_points": [],
            "suggested_action": "",
            "approval_question": f"이 {'운동' if plan_type == 'workout' else '식단'} 플랜으로 수정할까요?",
            "search_grounding_summary": "",
        }
    )
    draft_text = render_draft_preview(components)
    return components, draft_text, proposed_plan, str(plan_type), "update"


def _expand_long_range_plan_if_requested(
    state: GraphState,
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
) -> list[dict]:
    if proposed_plan_type not in {"workout", "diet"} or not proposed_plan:
        return proposed_plan

    target_days = _requested_plan_days(_resolved_user_message(state))
    if not target_days:
        return proposed_plan

    proposed_plan = _align_plan_start_to_today_if_implicit(state, proposed_plan)

    unique_dates = _plan_unique_iso_days(proposed_plan)
    if len(unique_dates) >= min(target_days, 21):
        return proposed_plan

    start_day = _plan_start_day(proposed_plan)
    if proposed_plan_type == "diet":
        return _expand_diet_plan_days(proposed_plan, start_day, target_days)
    return _expand_workout_plan_days(proposed_plan, start_day, target_days)


def _requested_plan_days(message: str) -> int | None:
    lowered = re.sub(r"\s+", "", str(message or "").lower())
    spaced = str(message or "").lower()

    if any(marker in lowered for marker in ("한달", "1달", "1개월", "월간", "monthly", "onemonth")):
        return 30
    if re.search(r"30\s*일", spaced):
        return 30

    if any(marker in lowered for marker in ("일주일", "한주", "일주", "weekly", "oneweek", "1week")):
        return 7

    week_match = re.search(r"(\d+)\s*주", spaced)
    if week_match:
        weeks = int(week_match.group(1))
        if 1 <= weeks <= 6:
            return min(weeks * 7, 31)

    day_match = re.search(r"(\d+)\s*일", spaced)
    if day_match:
        days = int(day_match.group(1))
        if 7 <= days <= 31:
            return days

    month_match = re.search(r"(\d+)\s*(?:달|개월|month)", spaced)
    if month_match and int(month_match.group(1)) >= 1:
        return 30

    return None


def _align_plan_start_to_today_if_implicit(state: GraphState, plan_items: list[dict]) -> list[dict]:
    if not plan_items:
        return plan_items
    message = _resolved_user_message(state)
    if _has_explicit_plan_start_date(message):
        return plan_items

    today = _parse_iso_date(kst_today_iso())
    if today is None:
        return plan_items

    start_day = _plan_start_day(plan_items)
    if start_day == today:
        return plan_items

    delta = today - start_day
    aligned: list[dict] = []
    for item in plan_items:
        copied = dict(item)
        parsed = _parse_iso_date(str(copied.get("day") or "").strip()[:10])
        if parsed is not None:
            copied["day"] = (parsed + delta).isoformat()
        copied["ex_list"] = [dict(exercise) for exercise in copied.get("ex_list") or []]
        aligned.append(copied)
    return aligned or plan_items


def _has_explicit_plan_start_date(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if re.search(r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}", normalized):
        return True
    if re.search(r"\d{1,2}\s*월\s*\d{1,2}\s*일", normalized):
        return True
    return any(
        marker in compact
        for marker in (
            "내일부터",
            "모레부터",
            "다음주",
            "차주",
            "다음달",
            "월요일부터",
            "화요일부터",
            "수요일부터",
            "목요일부터",
            "금요일부터",
            "토요일부터",
            "일요일부터",
        )
    )


def _plan_unique_iso_days(plan_items: list[dict]) -> set[str]:
    days: set[str] = set()
    for item in plan_items:
        if not isinstance(item, dict):
            continue
        day_text = str(item.get("day") or "").strip()[:10]
        if _parse_iso_date(day_text):
            days.add(day_text)
    return days


def _plan_start_day(plan_items: list[dict]) -> date:
    parsed_days = [
        parsed
        for item in plan_items
        if isinstance(item, dict)
        for parsed in [_parse_iso_date(str(item.get("day") or "").strip()[:10])]
        if parsed is not None
    ]
    if parsed_days:
        return min(parsed_days)
    return _parse_iso_date(kst_today_iso()) or date.today()


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _expand_diet_plan_days(plan_items: list[dict], start_day: date, target_days: int) -> list[dict]:
    patterns = _daily_plan_patterns(plan_items, max_items_per_pattern=4)
    if not patterns:
        return plan_items

    expanded: list[dict] = []
    for offset in range(target_days):
        current_day = (start_day + timedelta(days=offset)).isoformat()
        pattern = patterns[offset % len(patterns)]
        for item in pattern:
            next_item = _copy_plan_item_for_day(item, current_day)
            next_item["ex_list"] = []
            expanded.append(next_item)
    return expanded or plan_items


def _expand_workout_plan_days(plan_items: list[dict], start_day: date, target_days: int) -> list[dict]:
    sessions = [dict(item) for item in plan_items if isinstance(item, dict)]
    if not sessions:
        return plan_items

    weekly_offsets = _workout_weekly_offsets(len(sessions))
    expanded: list[dict] = []
    for week_start in range(0, target_days, 7):
        for index, item in enumerate(sessions):
            offset = week_start + weekly_offsets[index % len(weekly_offsets)]
            if offset >= target_days:
                continue
            current_day = (start_day + timedelta(days=offset)).isoformat()
            expanded.append(_copy_plan_item_for_day(item, current_day))
    if target_days <= 7:
        expanded = _fill_weekly_workout_rest_days(expanded, start_day, target_days)
    return expanded or plan_items


def _fill_weekly_workout_rest_days(plan_items: list[dict], start_day: date, target_days: int) -> list[dict]:
    if target_days > 7:
        return plan_items

    existing_days = {
        str(item.get("day") or "").strip()[:10]
        for item in plan_items
        if isinstance(item, dict) and _parse_iso_date(str(item.get("day") or "").strip()[:10])
    }
    completed = list(plan_items)
    for offset in range(target_days):
        current_day = (start_day + timedelta(days=offset)).isoformat()
        if current_day in existing_days:
            continue
        completed.append(
            {
                "name": "휴식 또는 가벼운 스트레칭",
                "detail": "회복 중심",
                "day": current_day,
                "ex_list": [
                    {
                        "exercise_name": "가벼운 전신 스트레칭",
                        "sets": 1,
                        "calories": 20,
                    }
                ],
            }
        )
    return sorted(completed, key=lambda item: str(item.get("day") or ""))


def _daily_plan_patterns(plan_items: list[dict], *, max_items_per_pattern: int) -> list[list[dict]]:
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for index, item in enumerate(plan_items):
        if not isinstance(item, dict):
            continue
        key = str(item.get("day") or "").strip() or f"pattern-{index // max_items_per_pattern}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(dict(item))

    return [
        grouped[key][:max_items_per_pattern]
        for key in order
        if grouped.get(key)
    ]


def _workout_weekly_offsets(session_count: int) -> list[int]:
    if session_count <= 1:
        return [0]
    if session_count == 2:
        return [0, 3]
    if session_count == 3:
        return [0, 2, 4]
    if session_count == 4:
        return [0, 1, 3, 5]
    return [0, 1, 2, 4, 5, 6, 3]


def _copy_plan_item_for_day(item: dict, day: str) -> dict:
    copied = dict(item)
    copied.pop("id", None)
    copied["day"] = day
    copied["ex_list"] = [dict(exercise) for exercise in copied.get("ex_list") or []]
    return copied


def _memory_results(state: GraphState) -> list[dict]:
    return [
        result
        for result in state.get("search_results") or []
        if result.get("source") in {"memory", "important"} and str(result.get("text") or "").strip()
    ]


def _memory_grounding_note(state: GraphState) -> str:
    results = _memory_results(state)
    if not results:
        return ""
    labels = {
        "memory": "장기 기억",
        "important": "중요 프로필 기억",
    }
    source_labels = sorted({labels.get(str(result.get("source")), "기억") for result in results})
    snippets = [str(result.get("text") or "").strip()[:60] for result in results[:2]]
    return f"{'/'.join(source_labels)}에서 확인된 선호와 제약({'; '.join(snippets)})을 함께 반영했어요."


def _build_direct_past_memory_draft(state: GraphState) -> dict | None:
    if not state.get("requires_past_memory"):
        return None

    results = _memory_results(state)
    if results:
        snippets = [str(result.get("text") or "").strip() for result in results[:3]]
        components = normalize_draft_components(
            {
                "core_message": "저장된 기억 기준으로 확인해보면, 아래 내용이 남아 있어요.",
                "reason_points": [snippet[:140] for snippet in snippets],
                "suggested_action": "이 기억을 바탕으로 운동이나 식단 계획을 다시 맞춰드릴 수 있어요.",
                "search_grounding_summary": _memory_grounding_note(state),
            }
        )
    else:
        components = normalize_draft_components(
            {
                "core_message": "저장된 장기 기억에서는 아직 확인되는 내용이 없어요.",
                "reason_points": ["방금/아까 말한 내용이면 최근 대화에서 다시 확인할 수 있고, 앞으로 남길 내용은 '기억해줘'라고 말하면 됩니다."],
                "suggested_action": "기억해둘 선호, 제약, 목표가 있으면 한 문장으로 알려주세요.",
                "search_grounding_summary": "",
            }
        )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": [],
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _build_direct_short_term_memory_draft(state: GraphState) -> dict | None:
    if not state.get("short_term_memory_query"):
        return None

    user_message = str(state.get("user_message") or "")
    alias = _extract_recent_alias(state)
    if alias and "\ubcc4\uba85" not in user_message:
        alias = None

    if alias:
        components = normalize_draft_components(
            {
                "core_message": f"\ubc29\uae08 \ub9d0\ud55c \ubcc4\uba85\uc740 '{alias}'\uc774\uc5d0\uc694.",
                "reason_points": [f"\ucd5c\uadfc \ub300\ud654\uc5d0\uc11c '{alias}'\ub77c\uace0 \ub9d0\ud588\uc5b4\uc694."],
                "suggested_action": "",
                "search_grounding_summary": "",
            }
        )
        return {
            "draft_response": render_draft_preview(components),
            "draft_components": components,
            "proposed_plan": [],
            "proposed_plan_type": None,
            "proposed_plan_action": None,
            "self_eval_count": 0,
            "self_eval_failure_reason": None,
        }

    recent_user_message = _latest_previous_user_message(state)
    if recent_user_message and _looks_like_recent_utterance_query(user_message):
        components = normalize_draft_components(
            {
                "core_message": f"\ubc29\uae08 \ub9d0\ud558\uc2e0 \ub0b4\uc6a9\uc740 \"{recent_user_message[:120]}\"\uc608\uc694.",
                "reason_points": [],
                "suggested_action": "",
                "search_grounding_summary": "",
            }
        )
        return {
            "draft_response": render_draft_preview(components),
            "draft_components": components,
            "proposed_plan": [],
            "proposed_plan_type": None,
            "proposed_plan_action": None,
            "self_eval_count": 0,
            "self_eval_failure_reason": None,
        }

    return None


def _extract_recent_alias(state: GraphState) -> str | None:
    alias_pattern = re.compile(
        r"(?:\ub0b4\s*\ubcc4\uba85(?:\uc740|\uc774)?|(?:\uc55e\uc73c\ub85c\s*)?\ub0b4\s*\ubcc4\uba85(?:\uc740|\uc774)?)\s*(?::|=)?\s*([^\n,.!?]+)"
    )
    for user_text in reversed(_recent_user_texts(state)):
        match = alias_pattern.search(user_text)
        if match:
            return match.group(1).strip(" \"'")
    return None

def _latest_previous_user_message(state: GraphState) -> str:
    recent_user_texts = _recent_user_texts(state)
    if recent_user_texts:
        return recent_user_texts[-1].strip()
    return ""

def _looks_like_recent_utterance_query(user_message: str) -> bool:
    normalized = re.sub(r"\s+", " ", user_message.strip().lower())
    if not normalized:
        return False
    markers = (
        "\ubc29\uae08",
        "\uc544\uae4c",
        "\uc870\uae08 \uc804",
        "\ub0b4\uac00 \ub9d0\ud55c",
        "\ubb50\ub77c\uace0 \ud588",
        "\ubb50\ub77c\uace0 \ub9d0\ud588",
    )
    return any(marker in normalized for marker in markers)


def _needs_generate_retry(state: GraphState, draft_result: DraftResponse) -> bool:
    if _should_retry_for_missing_plan(state, draft_result):
        return True

    if state.get("short_term_memory_query") and draft_result.search_grounding_summary:
        return True

    last_assistant_message = _latest_assistant_reference(state)
    if not last_assistant_message:
        return False

    current_text = render_draft_preview(_build_components_from_result(draft_result, state))
    return _is_too_similar(current_text, last_assistant_message)


def _is_too_similar(current_text: str, previous_text: str) -> bool:
    current_tokens = set(_normalized_overlap_tokens(current_text))
    previous_tokens = set(_normalized_overlap_tokens(previous_text))
    if not current_tokens or not previous_tokens:
        return False
    if current_tokens == previous_tokens:
        return True
    overlap = len(current_tokens & previous_tokens) / max(len(current_tokens), len(previous_tokens))
    return overlap >= _REPETITION_OVERLAP_THRESHOLD


def _should_retry_for_missing_plan(state: GraphState, draft_result: DraftResponse) -> bool:
    if state.get("short_term_memory_query"):
        return False
    if state.get("intent") not in {INTENT_PLAN, INTENT_MODIFY}:
        return False
    if draft_result.proposed_plan:
        return False
    return not bool(state.get("needs_clarification"))


def _normalized_overlap_tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return re.findall(r"[0-9a-z\uac00-\ud7a3]+", normalized)


def _resolved_user_message(state: GraphState) -> str:
    resolution = state.get("context_resolution") or {}
    resolved_text = str(resolution.get("resolved_text") or "").strip()
    resolved_reference = resolution.get("resolved_reference")
    confidence = float(resolution.get("confidence") or 0.0)
    if resolved_reference and resolved_reference != "none" and resolved_text and confidence >= 0.6:
        return resolved_text
    return str(state.get("user_message") or "")


def _recent_dialogue_history(state: GraphState, *, limit: int = _RECENT_DIALOGUE_HISTORY_LIMIT) -> str:
    recent_turns = ((state.get("recent_dialogue") or {}).get("recent_turns") or [])[-limit:]
    if not recent_turns:
        return ""

    lines: list[str] = []
    for turn in recent_turns:
        user_text = str(turn.get("user_summary") or turn.get("user_text") or "").strip()
        assistant_text = str(turn.get("assistant_summary") or turn.get("assistant_text") or "").strip()
        if user_text:
            lines.append(f"user: {user_text[:240]}")
        if assistant_text:
            lines.append(f"assistant: {assistant_text[:240]}")
    return "\n".join(lines)


def _latest_assistant_reference(state: GraphState) -> str:
    recent_turns = (state.get("recent_dialogue") or {}).get("recent_turns") or []
    for turn in reversed(recent_turns):
        assistant_text = str(turn.get("assistant_text") or "").strip()
        if assistant_text:
            return assistant_text
    return ""


def _recent_user_texts(state: GraphState) -> list[str]:
    recent_turns = (state.get("recent_dialogue") or {}).get("recent_turns") or []
    return [
        str(turn.get("user_text") or "").strip()
        for turn in recent_turns
        if str(turn.get("user_text") or "").strip()
    ]


def _build_approval_draft(state: GraphState) -> dict:
    proposed_plan = state.get("proposed_plan") or []
    if not proposed_plan:
        components = normalize_draft_components(
            {
                "core_message": "현재 승인 대기 중인 계획이 없습니다.",
                "suggested_action": "먼저 계획 제안을 받은 뒤 승인 요청을 해 주세요.",
            }
        )
        return {
            "draft_response": render_draft_preview(components),
            "draft_components": components,
            "proposed_plan": None,
            "proposed_plan_type": None,
            "proposed_plan_action": None,
            "self_eval_count": 0,
            "self_eval_failure_reason": None,
        }

    proposed_plan_type = state.get("proposed_plan_type")
    proposed_plan_action = state.get("proposed_plan_action") or "create"
    plan_label = "식단" if proposed_plan_type == "diet" else "운동"
    action_label = "수정안" if proposed_plan_action == "update" else "계획"

    components = normalize_draft_components(
        {
            "core_message": f"{plan_label} {action_label} 승인을 확인했다.",
            "suggested_action": "이제 저장 절차를 진행한다.",
            "search_grounding_summary": "",
        }
    )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": proposed_plan,
        "proposed_plan_type": proposed_plan_type,
        "proposed_plan_action": proposed_plan_action,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _build_approval_draft_v2(state: GraphState) -> dict:
    proposed_plan = state.get("proposed_plan") or []
    if not proposed_plan:
        components = normalize_draft_components(
            {
                "core_message": "지금은 승인 대기 중인 계획이 없어요.",
                "suggested_action": "먼저 계획을 제안받은 뒤 승인 요청을 해주세요.",
            }
        )
        return {
            "draft_response": render_draft_preview(components),
            "draft_components": components,
            "proposed_plan": None,
            "proposed_plan_type": None,
            "proposed_plan_action": None,
            "self_eval_count": 0,
            "self_eval_failure_reason": None,
        }

    proposed_plan_type = state.get("proposed_plan_type")
    proposed_plan_action = state.get("proposed_plan_action") or "create"
    plan_label = "식단" if proposed_plan_type == "diet" else "운동"
    action_label = "수정안" if proposed_plan_action == "update" else "계획"

    components = normalize_draft_components(
        {
            "core_message": f"{plan_label} {action_label} 반영할게.",
            "suggested_action": "",
            "search_grounding_summary": "",
        }
    )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": proposed_plan,
        "proposed_plan_type": proposed_plan_type,
        "proposed_plan_action": proposed_plan_action,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _build_plan_delete_draft(state: GraphState) -> dict:
    payload = state.get("profile_changes") or {}
    target_dates = payload.get("target_dates") or []
    plan_type = payload.get("plan_type") or state.get("domain") or "all"
    plan_label = {
        "workout": "운동",
        "diet": "식단",
        "all": "운동/식단",
    }.get(str(plan_type), "플랜")
    date_label = _format_delete_date_label(target_dates)

    components = normalize_draft_components(
        {
            "core_message": f"{date_label} {plan_label} 플랜을 삭제할게.",
            "reason_points": ["요청한 날짜와 플랜 종류만 캘린더에서 제거합니다."],
            "suggested_action": "",
            "approval_question": None,
            "search_grounding_summary": "",
        }
    )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": [],
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _format_delete_date_label(target_dates: list[str]) -> str:
    if not target_dates:
        return "요청한 날짜의"
    if len(target_dates) == 1:
        return f"{target_dates[0]}의"
    return f"{target_dates[0]}부터 {target_dates[-1]}까지"


def _build_care_draft(state: GraphState) -> dict:
    profile = _effective_user_profile(state)
    components = normalize_draft_components(
        {
            "core_message": "못 한 게 문제가 아니라 다시 시작할 수 있게 부담을 줄이는 게 우선이에요.",
            "reason_points": [
                _profile_fit_note(profile) or "지금은 큰 계획보다 바로 할 수 있는 작은 행동이 더 잘 맞아요.",
                "오늘은 운동이나 식단을 완벽히 맞추기보다 5~10분 산책, 물 한 컵, 한 끼 균형처럼 낮은 기준으로 충분해요.",
            ],
            "suggested_action": "오늘 할 일은 하나만 고르세요. 너무 버거우면 쉬는 것도 계획의 일부로 둘게요.",
            "safety_notes": _profile_safety_notes(profile, None),
            "approval_question": None,
            "search_grounding_summary": _constraint_grounding_note(profile, None),
        }
    )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": [],
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _build_casual_draft(state: GraphState) -> dict:
    profile = _effective_user_profile(state)
    components = normalize_draft_components(
        {
            "core_message": "알겠어요. 지금 알려준 상황과 제약을 기준으로 답할게요.",
            "reason_points": [
                _profile_fit_note(profile) or "다음 질문에서는 현재 맥락을 이어서 반영할게요.",
            ],
            "suggested_action": "운동, 식단, 통증, 피해야 할 것 중 궁금한 걸 바로 물어봐 주세요.",
            "safety_notes": _profile_safety_notes(profile, None),
            "approval_question": None,
            "search_grounding_summary": _constraint_grounding_note(profile, None),
        }
    )
    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": [],
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _build_safety_draft(state: GraphState) -> dict:
    safety_kind = _classify_safety_kind(state["user_message"])

    if safety_kind == "mental_health_crisis":
        components = normalize_draft_components(
            {
                "core_message": "지금 혼자 버티지 말고 즉시 사람과 연결되는 것이 우선입니다.",
                "reason_points": [
                    "현재 메시지는 자해나 자살 위험처럼 즉각적인 도움 연결이 필요한 상황으로 보입니다.",
                    "운동이나 식단 조언보다 안전 확보와 주변 도움 요청이 먼저입니다.",
                ],
                "suggested_action": (
                    "가까운 사람에게 지금 상태를 바로 알리고, 자살예방상담전화 109 "
                    "또는 정신건강상담전화 1577-0199에 즉시 연락하세요."
                ),
                "safety_notes": [
                    "혼자 있지 말고 주변 사람이나 보호자와 함께 있으세요.",
                    "위험한 물건이나 약물이 손에 닿지 않게 멀리하세요.",
                    "당장 위험이 크면 119 또는 가까운 응급실로 바로 도움을 요청하세요.",
                ],
                "approval_question": None,
                "search_grounding_summary": "",
            }
        )
    elif safety_kind == "extreme_diet":
        components = normalize_draft_components(
            {
                "core_message": "식사를 거르거나 단기간에 큰 폭으로 감량하는 방식은 안전하지 않아서 도와드릴 수 없어요.",
                "reason_points": [
                    "극단적인 제한은 어지럼, 폭식 반동, 근손실, 컨디션 저하 위험을 키울 수 있습니다.",
                    "감량은 식사를 유지하면서 작은 칼로리 조정과 활동량 조절로 가는 편이 안전합니다.",
                ],
                "suggested_action": "오늘은 끼니를 거르지 않는 균형 식사와 10~20분 가벼운 걷기부터 잡아볼게요.",
                "safety_notes": [
                    "최근 어지럼, 실신감, 폭식/절식 반복, 월경 이상, 복용약이나 질환이 있으면 전문가 상담을 우선하세요.",
                    "일주일에 큰 폭의 감량을 목표로 굶는 계획은 피하세요.",
                ],
                "approval_question": None,
                "search_grounding_summary": "",
            }
        )
    else:
        components = normalize_draft_components(
            {
                "core_message": "이건 운동 조언보다 즉시 응급 대응이 우선일 수 있는 증상입니다.",
                "reason_points": [
                    "가슴 통증, 호흡 곤란, 심한 어지럼, 실신감, 과다 복용 의심은 응급 평가가 필요할 수 있습니다.",
                    "운동을 계속하거나 집에서 버티는 것보다 즉시 중단하고 상태를 확인받는 것이 안전합니다.",
                ],
                "suggested_action": (
                    "지금 바로 운동을 멈추고 앉거나 누워 안정을 취한 뒤, 증상이 계속되면 119에 연락하거나 "
                    "가까운 응급실로 가세요."
                ),
                "safety_notes": [
                    "혼자 이동하지 말고 가능하면 주변 사람에게 도움을 요청하세요.",
                    "가슴 통증, 숨참, 의식 저하, 경련, 심한 출혈이 있으면 지체하지 말고 119를 부르세요.",
                    "증상이 가라앉더라도 원인 확인 전에는 운동을 다시 하지 마세요.",
                ],
                "approval_question": None,
                "search_grounding_summary": "",
            }
        )

    return {
        "draft_response": render_draft_preview(components),
        "draft_components": components,
        "proposed_plan": None,
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
    }


def _classify_safety_kind(message: str) -> str:
    if _MENTAL_HEALTH_SAFETY_PATTERNS.search(message):
        return "mental_health_crisis"
    if _EXTREME_DIET_SAFETY_PATTERNS.search(message):
        return "extreme_diet"
    if _PHYSICAL_SAFETY_PATTERNS.search(message):
        return "physical_emergency"
    return "physical_emergency"


def _resolve_proposed_plan_type(
    state: GraphState,
    draft_result: DraftResponse,
    proposed_plan: list[dict],
) -> str | None:
    if not proposed_plan:
        return None

    modify_target = state.get("modify_target")
    if modify_target in {"workout", "diet"}:
        return modify_target

    message_plan_type = _infer_plan_type_from_message(state["user_message"])
    if message_plan_type in {"workout", "diet"}:
        return message_plan_type

    item_plan_type = _infer_plan_type_from_items(proposed_plan)
    if item_plan_type in {"workout", "diet"}:
        return item_plan_type

    if draft_result.proposed_plan_type in {"workout", "diet"}:
        return draft_result.proposed_plan_type

    return None


def _infer_plan_type_from_items(proposed_plan: list[dict]) -> str | None:
    workout_count = 0
    diet_count = 0
    for item in proposed_plan or []:
        if not isinstance(item, dict):
            continue
        if item.get("ex_list"):
            workout_count += 1
        elif _is_diet_plan_item(item):
            diet_count += 1
    if workout_count and not diet_count:
        return "workout"
    if diet_count and not workout_count:
        return "diet"
    if workout_count > diet_count:
        return "workout"
    if diet_count > workout_count:
        return "diet"
    return None


def _resolve_proposed_plan_action(state: GraphState, proposed_plan: list[dict]) -> str | None:
    if not proposed_plan:
        return None
    if state.get("intent") == INTENT_MODIFY:
        return "update"
    return "create"


def _infer_plan_type_from_message(message: str) -> str | None:
    lowered = message.lower()
    explicit_workout = _has_explicit_workout_domain(lowered)
    explicit_diet = _has_explicit_diet_domain(lowered)
    if explicit_workout and not explicit_diet:
        return "workout"
    if explicit_diet and not explicit_workout:
        return "diet"
    if explicit_workout and explicit_diet:
        return None

    diet_hits = sum(1 for keyword in _PLAN_TYPE_KEYWORDS["diet"] if keyword in lowered)
    workout_hits = sum(1 for keyword in _PLAN_TYPE_KEYWORDS["workout"] if keyword in lowered)

    if diet_hits > workout_hits:
        return "diet"
    if workout_hits > diet_hits:
        return "workout"
    return None


async def _self_evaluate(deps: NodeDeps, state: GraphState, response: str) -> tuple[bool, str]:
    emotion = state.get("emotion") or {}
    user_content = (
        f"감정 상태: {emotion.get('label', '중립')} (강도 {emotion.get('intensity', 0):.1f})\n"
        f"사용자 메시지: {state['user_message']}\n"
        f"생성된 Draft:\n{response}"
    )

    try:
        raw = await deps.router.generate(
            system_prompt=_SELF_EVAL_PROMPT,
            user_content=user_content,
            response_schema=SelfEvalResponse,
        )
        result = SelfEvalResponse.model_validate_json(raw)
        return result.passed, result.reason
    except Exception as exc:
        logger.warning("Self-eval failed, passing through: %s", exc)
        return True, ""


def _apply_partial_patch(components: DraftComponents, reason: str) -> DraftComponents:
    patched = normalize_draft_components(dict(components))
    safety_line = "증상이 심하거나 위험하다고 느껴지면 전문가 상담이나 지역 응급 지원을 우선해 주세요."

    if safety_line not in patched["safety_notes"]:
        patched["safety_notes"].append(safety_line)

    if reason and not patched["search_grounding_summary"]:
        patched["search_grounding_summary"] = f"검토 메모: {reason}"

    return patched
