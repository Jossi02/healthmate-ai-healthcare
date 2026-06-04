"""Deterministic answer/profile-fit validation before finalization."""
from __future__ import annotations

import json
import time
import re
from datetime import date
from typing import Any

from app.core.diet_safety_rules import (
    COMMON_EATING_DISORDER_RISK_TERMS,
    COMMON_GOUT_PURINE_TERMS,
    COMMON_KIDNEY_HIGH_PROTEIN_TERMS,
    COMMON_PREGNANCY_FOOD_SAFETY_TERMS,
    COMMON_SODIUM_HEAVY_TERMS,
    COMMON_SUGAR_HEAVY_TERMS,
)
from app.core.intents import INTENT_APPROVAL, INTENT_MODIFY, INTENT_PLAN
from app.core.profile_constraints import as_text_list, profile_bmi_value, profile_weight_value
from app.graph.deps import NodeDeps
from app.schemas.home import HomeRecommendationResponse
from app.schemas.llm_responses import AnswerValidationJudgeResponse
from app.schemas.state import GraphState
from app.services.home_recommendations import (
    empty_home_recommendations,
    kst_today_iso,
    validate_home_recommendation_profile_fit,
)

_MAX_VALIDATION_RETRIES = 1
_PLAN_RATIONALE_PHRASES = (
    "알레르기 고려",
    "질환 고려",
    "제약 반영",
    "유제품 알레르기 고려",
    "프로필을 고려",
)

_ALLERGEN_TERMS = {
    "dairy_allergy": ("우유", "치즈", "요거트", "요구르트", "유제품", "버터", "크림", "milk", "cheese", "yogurt"),
    "egg_allergy": ("계란", "달걀", "egg"),
    "nut_allergy": ("견과", "땅콩", "아몬드", "호두", "peanut", "almond", "walnut", "nut"),
    "shellfish_allergy": ("새우", "게", "갑각류", "shellfish", "shrimp", "crab"),
    "wheat_allergy": ("밀", "빵", "파스타", "wheat", "gluten"),
    "soy_allergy": ("두부", "대두", "콩", "soy"),
}
_HIGH_IMPACT_TERMS = (
    "점프",
    "버피",
    "마운틴클라이머",
    "전력질주",
    "sprint",
    "jump",
    "jumping",
    "burpee",
    "plyo",
    "plyometric",
    "box jump",
    "플라이오",
    "점핑",
)
_BACK_LOAD_TERMS = ("데드리프트", "굿모닝", "무거운 스쿼트", "deadlift", "heavy squat")
_ADVANCED_WORKOUT_TERMS = (
    "hiit",
    "인터벌",
    "전력질주",
    "고강도",
    "고중량",
    "최대",
    "max",
    "5세트",
    "6세트",
    "tabata",
    "amrap",
    "emom",
    "agility",
    "metcon",
    "high intensity",
    "power circuit",
    "circuit",
    "fast tempo",
    "fast-paced",
    "quick repeat",
    "rapid repeat",
    "민첩성",
    "타바타",
    "서킷",
    "순환운동",
    "빠르게 반복",
    "빠른 반복",
    "고강도",
)
_LONG_WORKOUT_TERMS = ("60분", "70분", "80분", "90분", "1시간")
_SODIUM_HEAVY_TERMS = ("라면", "햄", "소시지", "베이컨", "짠", "나트륨", "젓갈", "국물", *COMMON_SODIUM_HEAVY_TERMS)
_SUGAR_HEAVY_TERMS = ("설탕", "시럽", "탄산", "주스", "디저트", "케이크", "과자", "달콤", *COMMON_SUGAR_HEAVY_TERMS)
_MEAT_TERMS = ("닭가슴살", "닭고기", "소고기", "돼지고기", "고기", "햄", "베이컨", "연어", "참치", "생선")
_VEGAN_CONFLICT_TERMS = (*_MEAT_TERMS, "계란", "달걀", "우유", "치즈", "요거트", "유제품")
_KIDNEY_DISEASE_HIGH_PROTEIN_TERMS = (
    "고단백",
    "프로틴",
    "단백질 쉐이크",
    "크레아틴",
    "식사대용 쉐이크",
    "단백질바",
    *COMMON_KIDNEY_HIGH_PROTEIN_TERMS,
    "닭가슴살 200",
    "닭가슴살 2",
)
_GOUT_PURINE_TERMS = ("내장", "곱창", "멸치", "정어리", "맥주", "조개", "새우", *COMMON_GOUT_PURINE_TERMS)
_PREGNANCY_RISK_TERMS = ("생선회", "회", "날달걀", "알코올", "술", "와인", "맥주", *COMMON_PREGNANCY_FOOD_SAFETY_TERMS)
_EATING_DISORDER_RISK_TERMS = (
    "900kcal",
    "800kcal",
    "단식",
    "굶",
    "하루 한 끼",
    "원푸드",
    "절식",
    *COMMON_EATING_DISORDER_RISK_TERMS,
)
_STRICT_PLAN_RAG_CONSTRAINTS = {
    "kidney_disease",
    "gout",
    "pregnancy",
    "eating_disorder_risk",
    "extreme_diet_risk",
}

_DAIRY_ALLOWED_REPLACEMENTS = (
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

_SEMANTIC_VALIDATION_PROMPT = """You are a strict semantic validator for a Korean fitness and nutrition assistant.
Return only the requested JSON schema.

Judge whether the answer and proposed_plan fit the user request, profile constraints, and retrieval need.
Focus on meaning, not exact keywords.

Mark critical only when there is a concrete issue that should block or regenerate:
- workout/diet domain is mixed or wrong
- allergy, dietary restriction, disease, pain, injury, age, weight, time, or frequency constraint is violated
- a plan request produced no actionable structured plan
- retrieval was required for a high-risk/specialized answer but the answer contradicts or ignores the provided evidence signals
- when retrieval_evidence_contract is present, verify the answer/proposed_plan is compatible with returned kb_ids and critical constraints
- persona wording changed concrete foods, exercises, dates, durations, sets, or safety constraints

Mark warning for mild verbosity, weak grounding, or small fit concerns.
If unsure, pass with no issues.
Use stable English issue codes such as semantic_profile_conflict, semantic_domain_mismatch, semantic_rag_ignored, semantic_persona_mutation.
"""


def make_answer_validator_node(deps: NodeDeps):
    async def answer_validator_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        report = _validate_state(state)
        if _has_deterministic_critical_issue(report):
            semantic_report = _skip_semantic_validation(deps, state, "deterministic_critical")
        else:
            semantic_report = await _semantic_validate_state(deps, state)
        report = _merge_validation_reports(report, semantic_report)
        report["quality_dimensions"] = _validation_quality_dimensions(state, report)
        retry_count = int(state.get("validation_retry_count", 0) or 0)
        can_retry = bool(report["requires_retry"] and retry_count < _MAX_VALIDATION_RETRIES)

        deps.trace.record_current_event(
            stage="answer_validator",
            status="ok" if report["passed"] else "warn",
            title="Answer validation completed",
            detail={
                "passed": report["passed"],
                "requires_retry": report["requires_retry"],
                "can_retry": can_retry,
                "issue_count": len(report["issues"]),
                "issues": report["issues"][:6],
                "quality_dimensions": report.get("quality_dimensions") or {},
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )

        if state.get("request_kind") == "home_recommendation" and not report["passed"]:
            flags = dict(state.get("generation_quality_flags") or {})
            flags["home_validator_blocked"] = True
            return {
                "validation_report": report,
                "home_recommendations": _empty_home_recommendations_for_state(state),
                "generation_quality_flags": flags,
                "force_regenerate": False,
                "self_eval_failure_reason": None,
            }

        if can_retry:
            reason = "; ".join(issue["message"] for issue in report["issues"][:3])
            return {
                "validation_report": report,
                "validation_retry_count": retry_count + 1,
                "response": None,
                "draft_response": None,
                "draft_components": None,
                "force_regenerate": True,
                "self_eval_failure_reason": f"validator: {reason}",
            }

        if not report["passed"]:
            safe_diet_fallback = _safe_diet_fallback_for_validation_failure(report, state)
            if safe_diet_fallback:
                return safe_diet_fallback
            safe_semantic_fallback = _safe_plan_fallback_for_semantic_failure(report, state)
            if safe_semantic_fallback:
                return safe_semantic_fallback
            return {
                "validation_report": report,
                "response": _blocked_response(report),
                "proposed_plan": [],
                "proposed_plan_type": None,
                "proposed_plan_action": None,
                "active_proposal": None,
                "awaiting_plan_confirmation": False,
                "force_regenerate": False,
                "needs_clarification": True,
                "self_eval_failure_reason": None,
            }

        return {
            "validation_report": report,
            "force_regenerate": False,
            "self_eval_failure_reason": None,
        }

    return answer_validator_node


def _safe_diet_fallback_for_validation_failure(
    report: dict[str, Any],
    state: GraphState,
) -> dict[str, Any] | None:
    if state.get("action_intent") not in {"create", "modify"}:
        return None
    if (state.get("proposed_plan_type") or state.get("domain")) != "diet":
        return None

    critical_codes = {
        str(issue.get("code"))
        for issue in report.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    }
    recoverable = {
        "allergen_conflict",
        "vegetarian_conflict",
        "vegan_conflict",
        "hypertension_sodium_conflict",
        "diabetes_sugar_conflict",
        "kidney_high_protein_conflict",
        "gout_purine_conflict",
        "pregnancy_food_safety_conflict",
        "eating_disorder_extreme_plan_conflict",
        "diet_plan_contains_ex_list",
        "diet_plan_missing_food_detail",
    }
    if not critical_codes or not critical_codes <= recoverable:
        return None

    proposed_plan = _build_safe_diet_fallback_items(state)
    plan_preview = _format_simple_plan_preview(proposed_plan)
    response = (
        "식단 플랜을 제안해요.\n"
        f"{plan_preview}\n"
        "이 식단 플랜으로 작성할까요?"
    )
    patched_report = dict(report)
    patched_report["passed"] = True
    patched_report["requires_retry"] = False
    patched_report["issues"] = [
        issue
        for issue in report.get("issues") or []
        if not (isinstance(issue, dict) and issue.get("severity") == "critical")
    ]
    patched_report["issues"].append(
        {
            "severity": "warning",
            "code": "safe_diet_fallback_applied",
            "message": "Diet proposal was replaced with a safe deterministic fallback.",
            "retry": False,
            "detail": {"recovered_codes": sorted(critical_codes)},
        }
    )
    flags = dict(state.get("generation_quality_flags") or {})
    flags["safe_diet_fallback_applied"] = True
    flags["safe_diet_fallback_recovered_codes"] = sorted(critical_codes)
    flags["safe_diet_fallback_revalidated"] = False
    fallback_state: GraphState = {
        **state,
        "response": response,
        "draft_response": response,
        "proposed_plan": proposed_plan,
        "proposed_plan_type": "diet",
        "proposed_plan_action": "create",
        "action_intent": "create",
        "domain": "diet",
        "needs_clarification": False,
        "generation_quality_flags": flags,
    }
    fallback_report = _validate_state(fallback_state)
    if not fallback_report.get("passed"):
        return None
    flags["safe_diet_fallback_revalidated"] = True
    flags["safe_diet_fallback_revalidation_issue_count"] = len(fallback_report.get("issues") or [])
    patched_report["issues"].append(
        {
            "severity": "warning",
            "code": "safe_diet_fallback_revalidated",
            "message": "Deterministic diet fallback passed profile and contract validation before returning.",
            "retry": False,
            "detail": {
                "fallback_issue_count": len(fallback_report.get("issues") or []),
                "quality_dimensions": fallback_report.get("quality_dimensions") or {},
            },
        }
    )
    return {
        "validation_report": patched_report,
        "response": response,
        "draft_response": response,
        "draft_components": {
            "core_message": "식단 플랜을 제안해요.",
            "plan_preview": plan_preview,
            "approval_question": "이 식단 플랜으로 작성할까요?",
            "reason_points": [],
            "safety_notes": [],
            "search_grounding_summary": "",
        },
        "proposed_plan": proposed_plan,
        "proposed_plan_type": "diet",
        "proposed_plan_action": "create",
        "awaiting_plan_confirmation": True,
        "generation_quality_flags": flags,
        "force_regenerate": False,
        "needs_clarification": False,
        "self_eval_failure_reason": None,
    }


def _build_safe_diet_fallback_items(state: GraphState) -> list[dict[str, Any]]:
    today = kst_today_iso()
    profile_text = _profile_request_constraint_text(state)
    plant_based = _contains_any(profile_text, ("vegetarian", "vegan", "plant based", "plant-based", "채식", "비건"))
    soy_free = _contains_any(profile_text, ("soy", "soybean", "대두", "콩 알레르기", "콩알레르기"))
    egg_free = _contains_any(profile_text, ("egg", "계란", "달걀"))
    fish_free = _contains_any(profile_text, ("fish", "seafood", "생선", "해산물"))
    diabetes = _contains_any(profile_text, ("diabetes", "blood sugar", "glucose", "당뇨", "혈당"))
    hypertension = _contains_any(profile_text, ("hypertension", "blood pressure", "고혈압", "혈압"))
    kidney = _contains_any(profile_text, ("kidney", "renal", "ckd", "신장", "콩팥", "만성신부전"))
    gout = _contains_any(profile_text, ("gout", "uric acid", "통풍", "요산"))
    pregnancy = _contains_any(profile_text, ("pregnancy", "pregnant", "임신", "임산부"))
    eating_risk = _contains_any(profile_text, ("eating disorder", "섭식", "폭식", "절식"))
    muscle = _contains_any(profile_text, ("muscle", "strength", "근육", "근력", "증량"))

    breakfast_protein = "병아리콩" if plant_based or egg_free else "삶은 달걀"
    lunch_protein = "렌틸콩볼" if plant_based or soy_free else "두부 스테이크"
    if soy_free and not plant_based:
        lunch_protein = "닭가슴살"
    dinner_protein = "렌틸콩 수프" if plant_based or soy_free else "두부 채소볶음"
    if soy_free and not plant_based:
        dinner_protein = "닭가슴살구이" if fish_free else "흰살생선구이"

    breakfast = f"현미죽, 블루베리, {breakfast_protein}"
    lunch = f"현미밥, {lunch_protein}, 구운 채소"
    dinner = f"{dinner_protein}, 고구마, 데친 채소"

    if diabetes:
        breakfast = f"현미죽, 블루베리, {breakfast_protein}"
        lunch = f"현미밥 반 공기, {lunch_protein}, 구운 채소"
        dinner = f"{dinner_protein}, 고구마 소량, 데친 채소"
    if hypertension:
        lunch = lunch.replace("구운 채소", "저염 구운 채소")
        dinner = dinner.replace("데친 채소", "저염 데친 채소")
    if kidney or gout or pregnancy or eating_risk:
        breakfast = "현미죽, 블루베리, 데친 채소"
        lunch = "현미밥 반 공기, 구운 채소, 올리브오일 샐러드"
        dinner = "고구마, 채소 수프, 무가당 과일 소량"
    elif muscle:
        lunch = f"{lunch}, 삶은 병아리콩"

    return [
        {"name": "아침", "detail": breakfast, "day": today, "ex_list": []},
        {"name": "점심", "detail": lunch, "day": today, "ex_list": []},
        {"name": "저녁", "detail": dinner, "day": today, "ex_list": []},
    ]


def _safe_plan_fallback_for_semantic_failure(
    report: dict[str, Any],
    state: GraphState,
) -> dict[str, Any] | None:
    if state.get("action_intent") not in {"create", "modify"}:
        return None
    critical = [
        issue
        for issue in report.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    ]
    if not critical:
        return None
    if not all(str(issue.get("code") or "").startswith("semantic_") for issue in critical):
        return None
    if not all((issue.get("detail") or {}).get("source") == "semantic_judge" for issue in critical):
        return None

    plan_type = state.get("proposed_plan_type") or state.get("domain")
    if plan_type == "diet":
        proposed_plan = _build_safe_diet_fallback_items(state)
        core_message = "?앸떒 ?뚮옖???쒖븞?댁슂."
        approval_question = "???앸떒 ?뚮옖?쇰줈 ?묒꽦?좉퉴??"
    elif plan_type == "workout":
        proposed_plan = _build_safe_workout_fallback_items(state)
        core_message = "?대룞 ?뚮옖???쒖븞?댁슂."
        approval_question = "???대룞 ?뚮옖?쇰줈 ?묒꽦?좉퉴??"
    else:
        return None

    recovered_codes = [str(issue.get("code") or "") for issue in critical]
    plan_preview = _format_simple_plan_preview(proposed_plan)
    response = f"{core_message}\n{plan_preview}\n{approval_question}"
    flags = dict(state.get("generation_quality_flags") or {})
    flags["semantic_fallback_applied"] = True
    flags["semantic_fallback_recovered_codes"] = sorted(set(recovered_codes))
    flags["semantic_fallback_revalidated"] = False
    fallback_state: GraphState = {
        **state,
        "response": response,
        "draft_response": response,
        "proposed_plan": proposed_plan,
        "proposed_plan_type": plan_type,
        "proposed_plan_action": "create",
        "action_intent": "create",
        "domain": plan_type,
        "needs_clarification": False,
        "generation_quality_flags": flags,
    }
    fallback_report = _validate_state(fallback_state)
    if not fallback_report.get("passed"):
        return None
    flags["semantic_fallback_revalidated"] = True
    flags["semantic_fallback_revalidation_issue_count"] = len(fallback_report.get("issues") or [])
    recovered_report = _recovered_validation_report(
        report,
        recovered_codes=recovered_codes,
        recovery_code="semantic_safe_plan_fallback_applied",
        recovery_message="Semantic judge failure was recovered with a deterministic safe fallback.",
    )
    recovered_report["issues"].append(
        {
            "severity": "warning",
            "code": "semantic_safe_plan_fallback_revalidated",
            "message": "Deterministic fallback passed profile and contract validation before returning.",
            "retry": False,
            "detail": {
                "fallback_issue_count": len(fallback_report.get("issues") or []),
                "quality_dimensions": fallback_report.get("quality_dimensions") or {},
            },
        }
    )
    return {
        "validation_report": recovered_report,
        "response": response,
        "draft_response": response,
        "draft_components": {
            "core_message": core_message,
            "plan_preview": plan_preview,
            "approval_question": approval_question,
            "reason_points": [],
            "safety_notes": [],
            "search_grounding_summary": "",
        },
        "proposed_plan": proposed_plan,
        "proposed_plan_type": plan_type,
        "proposed_plan_action": "create",
        "awaiting_plan_confirmation": True,
        "generation_quality_flags": flags,
        "force_regenerate": False,
        "needs_clarification": False,
        "self_eval_failure_reason": None,
    }


def _build_safe_workout_fallback_items(state: GraphState) -> list[dict[str, Any]]:
    today = kst_today_iso()
    profile = _effective_user_profile(state)
    available = _safe_int(profile.get("available_time_minutes"))
    cardio_minutes = max(5, min(available or 15, 15))
    return [
        {
            "name": "가벼운 전신 루틴",
            "detail": "낮은 강도의 전신 준비 운동",
            "day": today,
            "ex_list": [
                {"exercise_name": "의자 스쿼트", "sets": 2, "calories": 40},
                {"exercise_name": "벽 푸시업", "sets": 2, "calories": 35},
                {"exercise_name": "전신 스트레칭", "sets": 2, "calories": 20},
            ],
        },
        {
            "name": "가벼운 유산소",
            "detail": "무리 없는 걷기 중심 루틴",
            "day": today,
            "ex_list": [
                {"exercise_name": "편안한 걷기", "duration_minutes": cardio_minutes, "calories": 60},
            ],
        },
    ]


def _recovered_validation_report(
    report: dict[str, Any],
    *,
    recovered_codes: list[str],
    recovery_code: str,
    recovery_message: str,
) -> dict[str, Any]:
    patched_report = dict(report)
    patched_report["passed"] = True
    patched_report["requires_retry"] = False
    patched_report["issues"] = [
        issue
        for issue in report.get("issues") or []
        if not (isinstance(issue, dict) and issue.get("severity") == "critical")
    ]
    patched_report["issues"].append(
        {
            "severity": "warning",
            "code": recovery_code,
            "message": recovery_message,
            "retry": False,
            "detail": {"recovered_codes": sorted(set(recovered_codes))},
        }
    )
    semantic_judge = dict(patched_report.get("semantic_judge") or {})
    if semantic_judge:
        semantic_judge["recovered"] = True
        patched_report["semantic_judge"] = semantic_judge
    return patched_report


def _profile_request_constraint_text(state: GraphState) -> str:
    profile = _effective_user_profile(state)
    constraints = state.get("profile_constraints") or {}
    parts: list[str] = [str(state.get("user_message") or "")]
    for container in (profile, constraints):
        for value in container.values():
            if isinstance(value, (list, tuple, set)):
                parts.extend(str(item) for item in value)
            elif isinstance(value, dict):
                parts.extend(str(item) for item in value.values())
            else:
                parts.append(str(value))
    return " ".join(part for part in parts if part).lower()


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _format_simple_plan_preview(plan: list[dict[str, Any]]) -> str:
    lines = []
    for item in plan:
        lines.append(f"- {item.get('day')} {item.get('name')}: {item.get('detail')}")
    return "\n".join(lines)


def _validate_state(state: GraphState) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    response = str(state.get("response") or "").strip()
    intent = str(state.get("intent") or "")
    action_intent = str(state.get("action_intent") or "")
    proposed_plan = _safe_list(state.get("proposed_plan"))
    proposed_plan_type = state.get("proposed_plan_type")
    profile_constraints = _safe_dict(state.get("profile_constraints"))
    retrieval_decision = _safe_dict(state.get("retrieval_decision"))

    if state.get("request_kind") == "home_recommendation":
        return _validate_home_recommendation_state(state)

    if not response:
        _issue(issues, "critical", "missing_response", "최종 응답이 비어 있습니다.", retry=True)

    if (
        action_intent in {"create", "modify"}
        and state.get("ambiguous")
        and state.get("domain") == "general"
        and not proposed_plan
        and not state.get("needs_clarification")
    ):
        _issue(
            issues,
            "critical",
            "ambiguous_plan_domain",
            "Plan request domain is ambiguous.",
            retry=False,
            detail={"routing_diagnostics": state.get("routing_diagnostics") or {}},
        )

    if action_intent in {"create", "modify"} and not state.get("needs_clarification"):
        if not proposed_plan:
            _issue(issues, "critical", "missing_proposed_plan", "플랜 요청인데 proposed_plan이 없습니다.", retry=True)
        if proposed_plan and proposed_plan_type not in {"workout", "diet"}:
            _issue(issues, "critical", "missing_plan_type", "proposed_plan_type이 workout/diet으로 확정되지 않았습니다.", retry=True)

    if proposed_plan:
        _validate_plan_write_contract(issues, proposed_plan, proposed_plan_type)
        _validate_plan_domain(issues, state, proposed_plan, proposed_plan_type)
        _validate_profile_conflicts(issues, proposed_plan, proposed_plan_type, profile_constraints)
        _validate_profile_fit_details(issues, state, proposed_plan, proposed_plan_type, profile_constraints)

    _validate_generation_quality_flags(issues, state)

    if intent in {INTENT_PLAN, INTENT_MODIFY} and any(phrase in response for phrase in _PLAN_RATIONALE_PHRASES):
        _issue(issues, "warning", "verbose_profile_rationale", "플랜 답변에 불필요한 프로필 고려 설명이 노출되었습니다.", retry=False)

    if retrieval_decision.get("should_search") and state.get("search_quality") in {"weak", "degraded"}:
        _issue(issues, "warning", "weak_required_retrieval", "필요한 검색 근거 품질이 약합니다.", retry=False)

    _validate_rag_reflection(issues, state, profile_constraints)

    if action_intent == "approval" and intent == INTENT_APPROVAL and proposed_plan:
        _issue(issues, "warning", "approval_relisted_plan", "승인 응답에 플랜이 다시 노출될 수 있습니다.", retry=False)

    requires_retry = any(issue.get("retry") for issue in issues)
    passed = not any(issue["severity"] == "critical" for issue in issues)
    return _report(passed, issues, requires_retry)


def _validate_home_recommendation_state(state: GraphState) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    raw = state.get("home_recommendations")
    if not raw:
        _issue(
            issues,
            "critical",
            "missing_home_recommendations",
            "Home recommendation payload is missing.",
            retry=False,
        )
        return _report(False, issues, False)

    try:
        response = HomeRecommendationResponse.model_validate(raw)
    except Exception as exc:
        _issue(
            issues,
            "critical",
            "invalid_home_recommendations",
            "Home recommendation payload does not match the response contract.",
            retry=False,
            detail={"error": str(exc)},
        )
        return _report(False, issues, False)

    for issue in validate_home_recommendation_profile_fit(
        response,
        user_profile=_effective_user_profile(state),
    ):
        _issue(
            issues,
            str(issue.get("severity") or "warning"),
            str(issue.get("code") or "home_profile_conflict"),
            "Home recommendation conflicts with the user profile.",
            retry=False,
            detail={key: value for key, value in issue.items() if key not in {"severity", "code"}},
        )

    passed = not any(issue["severity"] == "critical" for issue in issues)
    return _report(passed, issues, False)


def _empty_home_recommendations_for_state(state: GraphState) -> dict[str, Any]:
    scope = state.get("home_recommendation_scope") or "all"
    if scope not in {"all", "workout", "diet"}:
        scope = "all"
    home_date = str(state.get("home_recommendation_date") or kst_today_iso())
    return empty_home_recommendations(
        date=home_date,
        scope=scope,
        user_profile=_effective_user_profile(state),
        today_plan=state.get("today_plan") or [],
        recent_recommendations=state.get("home_recommendation_recent") or {},
    ).model_dump()


def _has_deterministic_critical_issue(report: dict[str, Any]) -> bool:
    return any(issue.get("severity") == "critical" for issue in report.get("issues") or [])


def _skip_semantic_validation(deps: NodeDeps, state: GraphState, reason: str) -> None:
    deps.trace.record_current_event(
        stage="answer_validator.semantic_judge",
        status="info",
        title="Semantic validation skipped",
        detail={
            "reason": reason,
            "action_intent": state.get("action_intent"),
            "proposed_plan_count": len(state.get("proposed_plan") or []),
        },
    )
    return None


async def _semantic_validate_state(deps: NodeDeps, state: GraphState) -> dict[str, Any] | None:
    mode = _semantic_validation_mode(state)
    if mode == "skip":
        return _skip_semantic_validation(deps, state, "not_required")

    started_at = time.perf_counter()
    user_content = _semantic_validation_payload(state)
    try:
        raw = await deps.router.generate(
            system_prompt=_SEMANTIC_VALIDATION_PROMPT,
            user_content=user_content,
            response_schema=AnswerValidationJudgeResponse,
        )
        judged = AnswerValidationJudgeResponse.model_validate_json(raw)
    except Exception as exc:
        if _semantic_validation_strict_required(state):
            report = _report(
                False,
                [
                    {
                        "severity": "critical",
                        "code": "semantic_judge_unavailable",
                        "message": "Semantic validation was required but unavailable.",
                        "retry": True,
                        "detail": {"source": "semantic_judge", "error": str(exc)},
                    }
                ],
                True,
            )
            deps.trace.record_current_event(
                stage="answer_validator.semantic_judge",
                status="warn",
                title="Semantic validation unavailable in strict mode",
                detail={"error": str(exc), "strict": True},
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return report
        deps.trace.record_current_event(
            stage="answer_validator.semantic_judge",
            status="warn",
            title="Semantic validation unavailable",
            detail={"error": str(exc), "mode": mode},
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return None

    issues: list[dict[str, Any]] = []
    for issue in judged.issues[:6]:
        code = str(issue.code or "semantic_profile_fit").strip() or "semantic_profile_fit"
        severity = issue.severity if issue.severity in {"critical", "warning"} else "warning"
        retry = bool(issue.retry or severity == "critical")
        issues.append(
            {
                "severity": severity,
                "code": code,
                "message": issue.message or code,
                "retry": retry,
                "detail": {"source": "semantic_judge", "mode": mode},
            }
        )

    if mode == "observe":
        issues = [
            {
                **issue,
                "severity": "warning",
                "retry": False,
                "detail": {**(issue.get("detail") or {}), "observe_only": True},
            }
            for issue in issues
        ]

    report = _report(
        True if mode == "observe" else not any(issue["severity"] == "critical" for issue in issues),
        issues,
        False if mode == "observe" else any(issue.get("retry") for issue in issues),
    )
    report["mode"] = mode
    deps.trace.record_current_event(
        stage="answer_validator.semantic_judge",
        status="ok" if report["passed"] else "warn",
        title="Semantic validation completed",
        detail={
            "mode": mode,
            "passed": report["passed"],
            "judge_passed": judged.passed,
            "issue_count": len(issues),
            "issues": issues[:4],
        },
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )
    return report


def _should_run_semantic_validation(state: GraphState) -> bool:
    return _semantic_validation_mode(state) != "skip"


def _semantic_validation_mode(state: GraphState) -> str:
    if state.get("request_kind") == "home_recommendation":
        return "skip"
    if not str(state.get("response") or "").strip():
        return "skip"
    proposed_plan = _safe_list(state.get("proposed_plan"))
    action_intent = state.get("action_intent")
    retrieval_decision = _safe_dict(state.get("retrieval_decision"))
    profile_constraints = _safe_dict(state.get("profile_constraints"))
    field_coverage = _safe_dict(profile_constraints.get("profile_field_coverage"))
    rich_profile = (_safe_int(field_coverage.get("present_count")) or 0) >= 4
    high_risk_or_constrained = bool(
        profile_constraints.get("safety_risks")
        or profile_constraints.get("critical_constraints")
        or profile_constraints.get("hard_profile_constraints")
        or profile_constraints.get("request_hard_constraints")
    )
    should_run = bool(
        (proposed_plan and (rich_profile or high_risk_or_constrained))
        or (action_intent in {"create", "modify"} and high_risk_or_constrained)
        or retrieval_decision.get("requires_external")
    )
    if not should_run:
        return "skip"
    if action_intent in {"create", "modify"}:
        if (
            _plan_requires_strict_semantic_validation(profile_constraints)
            or high_risk_or_constrained
            or rich_profile
            or retrieval_decision.get("requires_external")
        ):
            return "blocking"
        return "observe"
    return "blocking"


def _semantic_validation_strict_required(state: GraphState) -> bool:
    profile_constraints = _safe_dict(state.get("profile_constraints"))
    return bool(
        _semantic_validation_mode(state) == "blocking"
        and (
            _requires_external_fail_closed(state, profile_constraints)
            or profile_constraints.get("safety_risks")
            or profile_constraints.get("critical_constraints")
        )
    )


def _plan_requires_strict_semantic_validation(profile_constraints: dict[str, Any]) -> bool:
    constraints = set(
        [
            *_safe_text_list(profile_constraints.get("critical_constraints")),
            *_safe_text_list(profile_constraints.get("retrieval_critical_constraints")),
            *_safe_text_list(profile_constraints.get("hard_profile_constraints")),
            *_safe_text_list(profile_constraints.get("request_hard_constraints")),
            *_safe_text_list(profile_constraints.get("safety_risks")),
        ]
    )
    return bool(constraints & _STRICT_PLAN_RAG_CONSTRAINTS)


def _semantic_validation_payload(state: GraphState) -> str:
    profile_constraints = _safe_dict(state.get("profile_constraints"))
    search_results = _safe_list(state.get("search_results"))
    proposed_plan = _safe_list(state.get("proposed_plan"))
    payload = {
        "user_message": state.get("user_message"),
        "intent": state.get("intent"),
        "action_intent": state.get("action_intent"),
        "domain": state.get("domain"),
        "profile_summary": _safe_dict(profile_constraints.get("summary")),
        "hard_profile_constraints": _safe_text_list(profile_constraints.get("hard_profile_constraints")),
        "request_hard_constraints": _safe_text_list(profile_constraints.get("request_hard_constraints")),
        "goals": _safe_text_list(profile_constraints.get("goals")),
        "retrieval_decision": _safe_dict(state.get("retrieval_decision")),
        "search_quality": state.get("search_quality"),
        "search_result_count": len(search_results),
        "search_results_preview": _search_results_for_judge(search_results),
        "retrieval_evidence_contract": _retrieval_evidence_contract(state),
        "proposed_plan_type": state.get("proposed_plan_type"),
        "proposed_plan": _plan_for_judge(proposed_plan),
        "generation_quality_flags": _safe_dict(state.get("generation_quality_flags")),
        "response": _truncate_text(state.get("response") or "", 2500),
    }

    return json.dumps(payload, ensure_ascii=False, default=str)


def _merge_validation_reports(base: dict[str, Any], semantic: dict[str, Any] | None) -> dict[str, Any]:
    if not semantic:
        return base
    issues = [*(base.get("issues") or []), *(semantic.get("issues") or [])]
    requires_retry = bool(base.get("requires_retry") or semantic.get("requires_retry") or any(issue.get("retry") for issue in issues))
    passed = not any(issue.get("severity") == "critical" for issue in issues)
    merged = dict(base)
    merged["passed"] = passed
    merged["requires_retry"] = requires_retry
    merged["issues"] = issues
    merged["semantic_judge"] = {
        "passed": semantic.get("passed"),
        "issue_count": len(semantic.get("issues") or []),
        "mode": semantic.get("mode") or "blocking",
    }
    return merged


def _plan_for_judge(plan: list[dict]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for item in plan[:10]:
        if not isinstance(item, dict):
            continue
        exercises: list[dict[str, Any]] = []
        for exercise in (item.get("ex_list") or [])[:4]:
            if not isinstance(exercise, dict):
                continue
            exercises.append(
                {
                    "exercise_name": _truncate_text(exercise.get("exercise_name") or "", 80),
                    "sets": exercise.get("sets"),
                    "duration_minutes": exercise.get("duration_minutes"),
                    "calories": exercise.get("calories"),
                }
            )
        preview.append(
            {
                "name": _truncate_text(item.get("name") or "", 80),
                "detail": _truncate_text(item.get("detail") or "", 180),
                "day": item.get("day"),
                "ex_list": exercises,
            }
        )
    return preview


def _search_results_for_judge(results: list[dict]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for result in results[:5]:
        if not isinstance(result, dict):
            continue
        metadata = _safe_dict(result.get("metadata"))
        preview.append(
            {
                "source": result.get("source"),
                "kb_id": result.get("kb_id") or metadata.get("kb_id"),
                "domain": result.get("domain") or metadata.get("domain"),
                "topic": result.get("topic") or metadata.get("topic"),
                "constraints": result.get("constraints") or metadata.get("constraints") or [],
                "goals": result.get("goals") or metadata.get("goals") or [],
                "text": _truncate_text(result.get("text") or "", 220),
            }
        )
    return preview


def _retrieval_evidence_contract(state: GraphState) -> dict[str, Any]:
    results = _safe_list(state.get("search_results"))
    profile_constraints = _safe_dict(state.get("profile_constraints"))
    retrieval_decision = _safe_dict(state.get("retrieval_decision"))
    draft_components = _safe_dict(state.get("draft_components"))
    kb_ids: list[str] = []
    returned_constraints: set[str] = set()
    for result in results[:8]:
        if not isinstance(result, dict):
            continue
        metadata = _safe_dict(result.get("metadata"))
        kb_id = str(result.get("kb_id") or metadata.get("kb_id") or "").strip()
        if kb_id:
            kb_ids.append(kb_id)
        values = result.get("constraints") or metadata.get("constraints") or []
        if isinstance(values, str):
            returned_constraints.add(values)
        elif isinstance(values, list):
            returned_constraints.update(str(value) for value in values if value)
    return {
        "requires_external": bool(retrieval_decision.get("requires_external")),
        "returned_kb_ids": kb_ids,
        "expected_critical_constraints": _safe_text_list(profile_constraints.get("retrieval_critical_constraints")),
        "returned_constraints": sorted(returned_constraints),
        "grounding_summary": draft_components.get("search_grounding_summary") or "",
    }


def _truncate_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _validate_plan_write_contract(
    issues: list[dict[str, Any]],
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
) -> None:
    if proposed_plan_type not in {"workout", "diet"}:
        return

    for index, item in enumerate(proposed_plan):
        if not isinstance(item, dict):
            _issue(issues, "critical", "plan_item_contract_invalid", "Plan item must be an object.", retry=True, detail={"index": index})
            continue

        name = str(item.get("name") or "").strip()
        day = str(item.get("day") or "").strip()
        detail = str(item.get("detail") or "").strip()
        item_plan_type = _item_write_plan_type(item, proposed_plan_type)
        if not name:
            _issue(issues, "critical", "plan_item_missing_name", "Plan item is missing name.", retry=True, detail={"index": index})
        if not _is_iso_date(day):
            _issue(issues, "critical", "plan_item_invalid_day", "Plan item day must be YYYY-MM-DD.", retry=True, detail={"index": index, "day": day})

        if item_plan_type == "workout":
            exercises = item.get("ex_list") or []
            if not isinstance(exercises, list) or not exercises:
                _issue(issues, "critical", "workout_plan_missing_ex_list", "Workout plan item must include ex_list.", retry=True, detail={"index": index, "name": name})
                continue
            for exercise_index, exercise in enumerate(exercises):
                if not isinstance(exercise, dict):
                    _issue(
                        issues,
                        "critical",
                        "workout_exercise_contract_invalid",
                        "Workout exercise entry must be an object.",
                        retry=True,
                        detail={"index": index, "exercise_index": exercise_index},
                    )
                    continue
                if not str(exercise.get("exercise_name") or "").strip():
                    _issue(
                        issues,
                        "critical",
                        "workout_exercise_missing_name",
                        "Workout exercise entry is missing exercise_name.",
                        retry=True,
                        detail={"index": index, "exercise_index": exercise_index},
                    )
                for numeric_key in ("sets", "duration_minutes", "calories"):
                    value = exercise.get(numeric_key)
                    if value in (None, ""):
                        continue
                    parsed = _safe_int(value)
                    if parsed is None or parsed < 0:
                        _issue(
                            issues,
                            "critical",
                            "workout_exercise_invalid_number",
                            "Workout exercise numeric fields must be non-negative numbers.",
                            retry=True,
                            detail={"index": index, "exercise_index": exercise_index, "field": numeric_key},
                        )

        if item_plan_type == "diet":
            if item.get("ex_list"):
                _issue(issues, "critical", "diet_plan_contains_ex_list", "Diet plan item must not include workout ex_list.", retry=True, detail={"index": index, "name": name})
            if not detail:
                _issue(issues, "critical", "diet_plan_missing_food_detail", "Diet plan item must include concrete food detail.", retry=True, detail={"index": index, "name": name})
            elif _is_weak_diet_detail(detail):
                _issue(issues, "warning", "diet_plan_weak_food_detail", "Diet plan food detail is too generic.", retry=False, detail={"index": index, "name": name, "detail": detail[:120]})


def _is_iso_date(value: str) -> bool:
    text = str(value or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    year, month, day = [int(part) for part in text.split("-")]
    if not 1900 <= year <= 2100:
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _is_weak_diet_detail(detail: str) -> bool:
    normalized = " ".join(str(detail or "").lower().split())
    if len(normalized) < 4:
        return True
    weak_values = {
        "meal",
        "diet",
        "healthy meal",
        "balanced meal",
        "light meal",
        "breakfast",
        "lunch",
        "dinner",
    }
    return normalized in weak_values


def _item_write_plan_type(item: dict, default_plan_type: str) -> str:
    if item.get("ex_list"):
        return "workout"
    inferred = _infer_plan_item_domain(item)
    if inferred in {"workout", "diet"}:
        return inferred
    return default_plan_type


def _validate_plan_domain(
    issues: list[dict[str, Any]],
    state: GraphState,
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
) -> None:
    expected = proposed_plan_type
    if expected not in {"workout", "diet"}:
        return
    mixed = []
    for item in proposed_plan:
        inferred = _infer_plan_item_domain(item)
        if inferred and inferred != expected:
            mixed.append({"name": item.get("name"), "inferred": inferred})
    if mixed:
        _issue(
            issues,
            "critical",
            "mixed_plan_domain",
            f"{expected} 플랜에 다른 도메인 항목이 섞였습니다.",
            retry=True,
            detail={"mixed": mixed[:5]},
        )

    state_domain = state.get("modify_target") or state.get("domain")
    if state_domain in {"workout", "diet"} and expected != state_domain and state.get("action_intent") in {"create", "modify"}:
        _issue(
            issues,
            "critical",
            "plan_type_domain_mismatch",
            f"요청 도메인({state_domain})과 제안 타입({expected})이 다릅니다.",
            retry=True,
        )


def _validate_profile_conflicts(
    issues: list[dict[str, Any]],
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
    profile_constraints: dict[str, Any],
) -> None:
    constraints = _hard_constraints_for_validation(profile_constraints)
    plan_text = _plan_text(proposed_plan).lower()

    if proposed_plan_type == "diet":
        for constraint, terms in _ALLERGEN_TERMS.items():
            if constraint in constraints and _contains_forbidden_allergen(plan_text, constraint, terms):
                _issue(
                    issues,
                    "critical",
                    "allergen_conflict",
                    f"{constraint} 제약과 충돌하는 식단 항목이 있습니다.",
                    retry=True,
                    detail={"constraint": constraint},
                )

    if proposed_plan_type == "workout":
        if "knee_pain" in constraints and any(term in plan_text for term in _HIGH_IMPACT_TERMS):
            _issue(
                issues,
                "critical",
                "knee_high_impact_conflict",
                "무릎 제약이 있는데 고충격 운동이 포함되었습니다.",
                retry=True,
            )
        if "back_pain" in constraints and any(term.lower() in plan_text for term in _BACK_LOAD_TERMS):
            _issue(
                issues,
                "critical",
                "back_load_conflict",
                "허리 제약이 있는데 부담 큰 운동이 포함되었습니다.",
                retry=True,
            )


def _contains_forbidden_allergen(
    plan_text: str,
    constraint: str,
    terms: tuple[str, ...],
) -> bool:
    normalized = str(plan_text or "").lower()
    if constraint == "dairy_allergy":
        for replacement in _DAIRY_ALLOWED_REPLACEMENTS:
            normalized = normalized.replace(replacement.lower(), "")
    return any(term.lower() in normalized for term in terms)


def _validate_rag_reflection(
    issues: list[dict[str, Any]],
    state: GraphState,
    profile_constraints: dict[str, Any],
) -> None:
    retrieval_decision = _safe_dict(state.get("retrieval_decision"))
    profile_constraints = _safe_dict(profile_constraints)
    if not retrieval_decision.get("requires_external") and not profile_constraints.get("should_use_rag"):
        return
    if state.get("search_quality") == "degraded":
        if _requires_external_fail_closed(state, profile_constraints):
            _issue(
                issues,
                "critical",
                "required_rag_degraded",
                "Required RAG evidence is degraded for a constrained or high-risk request.",
                retry=False,
                detail={"reason": retrieval_decision.get("reason")},
            )
        else:
            _issue(
                issues,
                "warning",
                "rag_degraded",
                "RAG evidence is degraded.",
                retry=False,
                detail={"reason": retrieval_decision.get("reason")},
            )
        return

    search_results = _safe_list(state.get("search_results"))
    draft_components = _safe_dict(state.get("draft_components"))
    has_grounding_summary = bool(str(draft_components.get("search_grounding_summary") or "").strip())
    if not search_results:
        _issue(
            issues,
            "warning",
            "required_rag_missing",
            "RAG가 필요한 요청이지만 검색 결과가 비어 있습니다.",
            retry=False,
            detail={"reason": retrieval_decision.get("reason")},
        )
        return

    returned_constraints = set()
    returned_kb_ids = []
    for result in search_results:
        if not isinstance(result, dict):
            continue
        metadata = _safe_dict(result.get("metadata"))
        kb_id = str(result.get("kb_id") or metadata.get("kb_id") or "").strip()
        if kb_id:
            returned_kb_ids.append(kb_id)
        values = result.get("constraints") or metadata.get("constraints") or []
        if isinstance(values, str):
            returned_constraints.add(values)
        elif isinstance(values, list):
            returned_constraints.update(str(value) for value in values if value)

    expected_constraints = set(_safe_text_list(profile_constraints.get("retrieval_critical_constraints")))
    reflected_constraints = bool(expected_constraints & returned_constraints) if expected_constraints else True
    if not reflected_constraints:
        _issue(
            issues,
            "warning",
            "rag_constraint_mismatch",
            "검색 결과의 핵심 제약 근거와 프로필 제약의 연결이 약합니다.",
            retry=False,
            detail={
                "expected_constraints": sorted(expected_constraints),
                "returned_constraints": sorted(returned_constraints)[:10],
            },
        )
    if retrieval_decision.get("requires_external") and not returned_kb_ids:
        _issue(
            issues,
            "warning",
            "rag_kb_id_missing",
            "Required retrieval returned evidence without stable kb_id identifiers.",
            retry=False,
        )
    if not has_grounding_summary:
        _issue(
            issues,
            "warning",
            "rag_grounding_not_visible",
            "검색 근거가 필요했지만 답변에 근거 요약 신호가 약합니다.",
            retry=False,
        )


def _validate_generation_quality_flags(
    issues: list[dict[str, Any]],
    state: GraphState,
) -> None:
    flags = state.get("generation_quality_flags") or {}
    persona_hits = flags.get("plan_persona_marker_hits") or []
    if persona_hits:
        _issue(
            issues,
            "critical",
            "plan_persona_data_contamination",
            "페르소나 말투가 proposed_plan 데이터에 섞였습니다.",
            retry=True,
            detail={"hits": persona_hits[:6]},
        )
    style_violations = flags.get("persona_style_violations") or []
    for violation in style_violations[:4]:
        if not isinstance(violation, dict):
            continue
        _issue(
            issues,
            str(violation.get("severity") or "warning"),
            str(violation.get("code") or "persona_style_violation"),
            "Persona style guard reported a response-shape violation.",
            retry=False,
            detail=violation,
        )


def _validate_profile_fit_details(
    issues: list[dict[str, Any]],
    state: GraphState,
    proposed_plan: list[dict],
    proposed_plan_type: str | None,
    profile_constraints: dict[str, Any],
) -> None:
    profile = _effective_user_profile(state)
    summary = profile_constraints.get("summary") or {}
    constraints = _hard_constraints_for_validation(profile_constraints)
    plan_text = _plan_text(proposed_plan).lower()

    if proposed_plan_type == "workout":
        available_minutes = _safe_int(profile.get("available_time_minutes") or summary.get("available_time_minutes"))
        if available_minutes:
            longest = max((_plan_item_duration_minutes(item) for item in proposed_plan), default=0)
            if not longest:
                _issue(
                    issues,
                    "warning",
                    "available_time_not_measurable",
                    "Available-time profile exists but workout duration is not measurable.",
                    retry=False,
                    detail={"available_time_minutes": available_minutes},
                )
            if longest and longest > max(available_minutes + 10, int(available_minutes * 1.35)):
                _issue(
                    issues,
                    "critical",
                    "available_time_exceeded",
                    "가능 시간보다 긴 운동 플랜이 포함되었습니다.",
                    retry=True,
                    detail={"available_time_minutes": available_minutes, "longest_item_minutes": longest},
                )

        frequency = _profile_frequency(profile)
        unique_days = len({str(item.get("day") or "") for item in proposed_plan if item.get("day")})
        if frequency and unique_days > frequency + 1:
            _issue(
                issues,
                "critical",
                "workout_frequency_exceeded",
                "프로필의 주간 운동 빈도보다 많은 운동일이 제안되었습니다.",
                retry=True,
                detail={"profile_frequency": frequency, "planned_days": unique_days},
            )

        level = _profile_level(profile)
        if level == "beginner" and any(term in plan_text for term in _ADVANCED_WORKOUT_TERMS):
            _issue(
                issues,
                "critical",
                "beginner_intensity_conflict",
                "초보/낮은 활동량 프로필에 고강도 운동이 포함되었습니다.",
                retry=True,
            )

        age = _safe_int(profile.get("age") or summary.get("age"))
        if age is not None and (age < 19 or age >= 60) and any(term in plan_text for term in _HIGH_IMPACT_TERMS):
            _issue(
                issues,
                "critical",
                "age_high_impact_conflict",
                "연령 프로필에 비해 고충격 운동이 포함되었습니다.",
                retry=True,
                detail={"age": age},
            )

        weight = profile_weight_value(profile)
        bmi = profile_bmi_value(profile)
        if (weight and weight >= 90 or bmi and bmi >= 25) and any(term in plan_text for term in _HIGH_IMPACT_TERMS):
            _issue(
                issues,
                "critical",
                "high_weight_high_impact_conflict",
                "고체중/BMI 프로필에 고충격 운동이 포함되었습니다.",
                retry=True,
                detail={"weight": weight, "bmi": bmi},
            )

        if _busy_lifestyle(profile) and any(term in plan_text for term in _LONG_WORKOUT_TERMS):
            _issue(
                issues,
                "warning",
                "busy_lifestyle_long_workout",
                "바쁜 생활 패턴에 비해 긴 운동 시간이 제안되었습니다.",
                retry=False,
            )

    if proposed_plan_type == "diet":
        if "hypertension" in constraints and any(term in plan_text for term in _SODIUM_HEAVY_TERMS):
            _issue(
                issues,
                "critical",
                "hypertension_sodium_conflict",
                "혈압 관리 제약과 충돌할 수 있는 고나트륨 식품이 포함되었습니다.",
                retry=True,
            )
        if "diabetes" in constraints and any(term in plan_text for term in _SUGAR_HEAVY_TERMS):
            _issue(
                issues,
                "critical",
                "diabetes_sugar_conflict",
                "혈당 관리 제약과 충돌할 수 있는 당류 중심 식품이 포함되었습니다.",
                retry=True,
            )
        if "vegetarian" in constraints and any(term in plan_text for term in _MEAT_TERMS):
            _issue(
                issues,
                "critical",
                "vegetarian_conflict",
                "채식 프로필과 충돌하는 식품이 포함되었습니다.",
                retry=True,
            )
        if "vegan" in constraints and any(term in plan_text for term in _VEGAN_CONFLICT_TERMS):
            _issue(
                issues,
                "critical",
                "vegan_conflict",
                "비건 프로필과 충돌하는 식품이 포함되었습니다.",
                retry=True,
            )
        if "kidney_disease" in constraints and any(term in plan_text for term in _KIDNEY_DISEASE_HIGH_PROTEIN_TERMS):
            _issue(
                issues,
                "critical",
                "kidney_high_protein_conflict",
                "신장 질환 제약에 비해 고단백 식단 신호가 강합니다.",
                retry=True,
            )
        if "gout" in constraints and any(term in plan_text for term in _GOUT_PURINE_TERMS):
            _issue(
                issues,
                "critical",
                "gout_purine_conflict",
                "통풍 제약과 충돌할 수 있는 고퓨린 식품이 포함되었습니다.",
                retry=True,
            )
        if "pregnancy" in constraints and any(term in plan_text for term in _PREGNANCY_RISK_TERMS):
            _issue(
                issues,
                "critical",
                "pregnancy_food_safety_conflict",
                "임신 프로필과 충돌할 수 있는 식품 안전 위험이 포함되었습니다.",
                retry=True,
            )
        if "eating_disorder_risk" in constraints and any(term in plan_text for term in _EATING_DISORDER_RISK_TERMS):
            _issue(
                issues,
                "critical",
                "eating_disorder_extreme_plan_conflict",
                "섭식 위험 프로필에 비해 제한적인 식단 표현이 포함되었습니다.",
                retry=True,
            )

    goals = set(profile_constraints.get("goals") or [])
    if proposed_plan_type == "workout" and "muscle_gain" in goals and not _contains_strength_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "muscle_goal_without_strength_signal",
            "근육 증가 목표인데 근력 운동 신호가 약합니다.",
            retry=False,
        )
    if proposed_plan_type == "workout" and "fat_loss" in goals and not _contains_cardio_or_fat_loss_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "fat_loss_goal_without_cardio_signal",
            "Fat-loss workout goal is present but cardio or energy-expenditure signals are weak.",
            retry=False,
        )
    if proposed_plan_type == "workout" and "mobility" in goals and not _contains_mobility_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "mobility_goal_without_mobility_signal",
            "Mobility workout goal is present but stretching or mobility signals are weak.",
            retry=False,
        )
    if proposed_plan_type == "diet" and {"weight_loss", "fat_loss"} & goals and not _contains_diet_structure(proposed_plan):
        _issue(
            issues,
            "warning",
            "weight_loss_goal_without_meal_structure",
            "감량 목표인데 식사 구성/칼로리 신호가 약합니다.",
            retry=False,
        )

    if proposed_plan_type == "diet" and "muscle_gain" in goals and not _contains_diet_protein_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "muscle_gain_diet_without_protein_signal",
            "Muscle-gain diet goal is present but concrete protein signals are weak.",
            retry=False,
        )
    if proposed_plan_type == "diet" and "glucose_control" in goals and not _contains_glucose_control_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "glucose_goal_without_stable_carb_signal",
            "Glucose-control diet goal is present but stable carbohydrate or fiber signals are weak.",
            retry=False,
        )
    if proposed_plan_type == "diet" and "heart_health" in goals and not _contains_heart_health_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "heart_health_goal_without_low_sodium_signal",
            "Heart-health diet goal is present but low-sodium or vegetable-forward signals are weak.",
            retry=False,
        )


def _infer_plan_item_domain(item: dict) -> str | None:
    exercises = item.get("ex_list") or []
    text = " ".join(
        [
            str(item.get("name") or ""),
            str(item.get("detail") or ""),
            *(str(ex.get("exercise_name") or "") for ex in exercises if isinstance(ex, dict)),
        ]
    ).lower()
    if exercises:
        return "workout"
    if any(keyword in text for keyword in ("breakfast", "lunch", "dinner", "yogurt", "salad", "salmon", "chicken", "meal")):
        return "diet"
    if any(keyword in text for keyword in ("아침", "점심", "저녁", "식사", "식단", "닭가슴살", "현미", "샐러드", "meal")):
        return "diet"
    if any(keyword in text for keyword in ("스쿼트", "런지", "푸시업", "걷기", "러닝", "스트레칭", "운동", "세트", "분")):
        return "workout"
    return None


def _has_mixed_workout_diet_plan(plan: list[dict]) -> bool:
    inferred = {_infer_plan_item_domain(item) for item in plan if isinstance(item, dict)}
    return "workout" in inferred and "diet" in inferred


def _plan_text(plan: list[dict]) -> str:
    chunks: list[str] = []
    for item in plan:
        chunks.extend(as_text_list(item.get("name")))
        chunks.extend(as_text_list(item.get("detail")))
        for exercise in item.get("ex_list") or []:
            if isinstance(exercise, dict):
                chunks.extend(as_text_list(exercise.get("exercise_name")))
    return " ".join(chunks)


def _hard_constraints_for_validation(profile_constraints: dict[str, Any]) -> set[str]:
    hard = [
        *(profile_constraints.get("hard_profile_constraints") or []),
        *(profile_constraints.get("request_hard_constraints") or []),
    ]
    if not hard and "hard_profile_constraints" not in profile_constraints and "request_hard_constraints" not in profile_constraints:
        hard = list(profile_constraints.get("constraints") or [])
    return set(hard)


def _plan_item_duration_minutes(item: dict) -> int:
    detail = str(item.get("detail") or "")
    exercise_total = 0
    for exercise in item.get("ex_list") or []:
        if not isinstance(exercise, dict):
            continue
        duration = _safe_int(exercise.get("duration_minutes"))
        if duration:
            exercise_total += duration
    if exercise_total:
        return exercise_total

    detail_minutes = sum(re_find_numbers_with_unit(detail, ("분", "minute", "minutes")))
    detail_minutes += sum(value * 60 for value in re_find_numbers_with_unit(detail, ("시간", "hour", "hours")))
    return detail_minutes


def re_find_numbers_with_unit(text: str, units: tuple[str, ...]) -> list[int]:
    unit_pattern = "|".join(re.escape(unit) for unit in units)
    return [int(float(match.group(1))) for match in re.finditer(rf"(\d+(?:\.\d+)?)\s*(?:{unit_pattern})", text, flags=re.IGNORECASE)]


def _profile_frequency(profile: dict[str, Any]) -> int | None:
    for key in (
        "exercise_frequency",
        "workout_frequency",
        "frequency_per_week",
        "weekly_workouts",
        "target_workouts_per_week",
    ):
        value = profile.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, list):
            return len(value)
        parsed = _safe_int(value)
        if parsed:
            return parsed
        text = str(value).lower()
        if "매일" in text or "daily" in text:
            return 7
        if "주" in text:
            numbers = re_find_numbers_with_unit(text, ("회", "번"))
            if numbers:
                return numbers[0]
    days = profile.get("preferred_workout_days")
    if isinstance(days, list) and days:
        return len(days)
    return None


def _profile_level(profile: dict[str, Any]) -> str:
    text = str(profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level") or "").lower()
    if any(keyword in text for keyword in ("beginner", "초보", "낮", "거의 없음", "가벼운", "앉아서", "low")):
        return "beginner"
    if any(keyword in text for keyword in ("advanced", "상급", "높", "격렬", "high")):
        return "advanced"
    return "unknown"


def _busy_lifestyle(profile: dict[str, Any]) -> bool:
    text = " ".join(
        str(profile.get(key) or "")
        for key in ("lifestyle", "schedule", "context_notes")
    ).lower()
    return any(keyword in text for keyword in ("바빠", "야근", "교대", "불규칙", "시간 부족", "busy", "shift"))


def _contains_strength_signal(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("근력", "웨이트", "스쿼트", "런지", "푸시업", "로우", "덤벨", "세트", "strength", "resistance"))


def _contains_cardio_or_fat_loss_signal(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("cardio", "walk", "walking", "run", "bike", "cycle", "aerobic", "fat loss", "calorie", "칼로리", "유산소", "걷기", "자전거", "인터벌"))


def _contains_mobility_signal(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("mobility", "stretch", "stretching", "warm-up", "cooldown", "가동성", "스트레칭", "유연성", "관절", "이완"))


def _contains_diet_structure(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("kcal", "칼로리", "단백질", "채소", "현미", "닭가슴살", "두부", "식사", "아침", "점심", "저녁"))


def _contains_diet_protein_signal(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("protein", "단백질", "닭가슴살", "두부", "렌틸콩", "병아리콩", "달걀", "생선", "콩", "그릭", "요거트"))


def _contains_glucose_control_signal(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("혈당", "당뇨", "저당", "통곡물", "현미", "귀리", "섬유", "채소", "고구마", "whole grain", "fiber", "low sugar"))


def _contains_heart_health_signal(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("저염", "나트륨", "채소", "과일", "통곡물", "현미", "dash", "혈압", "고혈압", "low sodium", "vegetable"))


def _requires_external_fail_closed(state: GraphState, profile_constraints: dict[str, Any]) -> bool:
    retrieval_decision = _safe_dict(state.get("retrieval_decision"))
    profile_constraints = _safe_dict(profile_constraints)
    if not retrieval_decision.get("requires_external") and not profile_constraints.get("should_use_rag"):
        return False
    action_intent = state.get("action_intent")
    if action_intent in {"create", "modify"}:
        if _plan_requires_strict_semantic_validation(profile_constraints):
            return True
        # Demo behavior: RAG enriches constrained plans, but a temporary Pinecone
        # miss must not prevent plan proposals when deterministic profile guards
        # can still enforce allergies, injuries, diseases, time, and frequency.
        return False
    constrained = bool(
        profile_constraints.get("safety_risks")
        or profile_constraints.get("critical_constraints")
        or profile_constraints.get("retrieval_critical_constraints")
        or profile_constraints.get("hard_profile_constraints")
        or profile_constraints.get("request_hard_constraints")
    )
    return constrained


def _effective_user_profile(state: GraphState) -> dict[str, Any]:
    return _safe_dict(state.get("effective_user_profile")) or _safe_dict(state.get("user_profile"))


_PROFILE_FIT_CODES = {
    "allergen_conflict",
    "knee_high_impact_conflict",
    "back_load_conflict",
    "available_time_exceeded",
    "workout_frequency_exceeded",
    "beginner_intensity_conflict",
    "age_high_impact_conflict",
    "high_weight_high_impact_conflict",
    "hypertension_sodium_conflict",
    "diabetes_sugar_conflict",
    "kidney_high_protein_conflict",
    "gout_purine_conflict",
    "pregnancy_food_safety_conflict",
    "eating_disorder_extreme_plan_conflict",
    "vegetarian_conflict",
    "vegan_conflict",
    "muscle_goal_without_strength_signal",
    "fat_loss_goal_without_cardio_signal",
    "mobility_goal_without_mobility_signal",
    "weight_loss_goal_without_meal_structure",
    "muscle_gain_diet_without_protein_signal",
    "glucose_goal_without_stable_carb_signal",
    "heart_health_goal_without_low_sodium_signal",
}
_GOAL_FIT_WARNING_CODES = {
    "muscle_goal_without_strength_signal",
    "fat_loss_goal_without_cardio_signal",
    "mobility_goal_without_mobility_signal",
    "weight_loss_goal_without_meal_structure",
    "muscle_gain_diet_without_protein_signal",
    "glucose_goal_without_stable_carb_signal",
    "heart_health_goal_without_low_sodium_signal",
}
_PLAN_CONTRACT_CODES = {
    "missing_proposed_plan",
    "missing_plan_type",
    "plan_item_contract_invalid",
    "plan_item_missing_name",
    "plan_item_invalid_day",
    "workout_plan_missing_ex_list",
    "workout_exercise_contract_invalid",
    "workout_exercise_missing_name",
    "workout_exercise_invalid_number",
    "diet_plan_contains_ex_list",
    "diet_plan_missing_food_detail",
}


def _validation_quality_dimensions(state: GraphState, report: dict[str, Any]) -> dict[str, Any]:
    issues = _safe_list(report.get("issues"))
    critical_codes = {
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    }
    warning_codes = {
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and issue.get("severity") == "warning"
    }
    profile_fit_warning_codes = sorted(warning_codes & _PROFILE_FIT_CODES)
    goal_fit_warning_codes = sorted(warning_codes & _GOAL_FIT_WARNING_CODES)
    critical_profile_fit_codes = sorted(critical_codes & _PROFILE_FIT_CODES)
    retrieval_decision = _safe_dict(state.get("retrieval_decision"))
    profile_constraints = _safe_dict(state.get("profile_constraints"))
    search_results = _safe_list(state.get("search_results"))
    draft_components = _safe_dict(state.get("draft_components"))
    grounding_summary = str(draft_components.get("search_grounding_summary") or "").strip()
    requires_external = bool(retrieval_decision.get("requires_external") or profile_constraints.get("should_use_rag"))
    search_quality = state.get("search_quality")
    retrieval_hit = (not requires_external) or bool(search_results)
    evidence_used = (not requires_external) or bool(grounding_summary or search_results)
    if not requires_external:
        evidence_status = "not_required"
    elif search_quality == "degraded":
        evidence_status = (
            "degraded"
            if _requires_external_fail_closed(state, profile_constraints)
            else "degraded_fail_open"
        )
    elif not search_results:
        evidence_status = "missing"
    elif grounding_summary or search_results:
        evidence_status = "grounded"
    else:
        evidence_status = "weak"
    return {
        "profile_fit_passed": not bool(critical_codes & _PROFILE_FIT_CODES),
        "critical_profile_fit_codes": critical_profile_fit_codes,
        "profile_fit_warning_codes": profile_fit_warning_codes,
        "profile_fit_warning_count": len(profile_fit_warning_codes),
        "goal_fit_warning_codes": goal_fit_warning_codes,
        "goal_fit_warning_count": len(goal_fit_warning_codes),
        "plan_write_contract_passed": not bool(critical_codes & _PLAN_CONTRACT_CODES),
        "retrieval_hit": retrieval_hit,
        "evidence_used": evidence_used,
        "evidence_status": evidence_status,
        "rag_fail_open": evidence_status == "degraded_fail_open",
        "semantic_judge": _safe_dict(report.get("semantic_judge")) or {"passed": None, "issue_count": 0},
        "requires_external": requires_external,
        "search_quality": search_quality,
        "profile_field_coverage": _safe_dict(profile_constraints.get("profile_field_coverage")),
    }


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_text_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [text for item in value if (text := str(item or "").strip())]
    text = str(value or "").strip()
    return [text] if text else []


def _safe_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value)
            if not match:
                return None
            value = match.group(0)
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    *,
    retry: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "message": message,
            "retry": retry,
            "detail": detail or {},
        }
    )


def _report(passed: bool, issues: list[dict[str, Any]], requires_retry: bool) -> dict[str, Any]:
    return {
        "version": "answer-validator-v1",
        "passed": passed,
        "requires_retry": requires_retry,
        "issues": issues,
    }


def _blocked_response(report: dict[str, Any]) -> str:
    critical = [
        issue
        for issue in report.get("issues", [])
        if issue.get("severity") == "critical"
    ]
    if any(issue.get("code") in {"allergen_conflict", "knee_high_impact_conflict", "back_load_conflict"} for issue in critical):
        return (
            "프로필 제약과 충돌할 수 있는 항목이 있어서 이 플랜은 그대로 진행하지 않을게요.\n"
            "운동/식단 종류와 꼭 피해야 할 조건을 한 번만 더 알려주면 안전하게 다시 작성할게요."
        )
    if any(issue.get("code") in {"mixed_plan_domain", "plan_type_domain_mismatch"} for issue in critical):
        return "운동/식단 구분이 섞여서 이 플랜은 확정하지 않을게요. 운동 플랜인지 식단 플랜인지 나눠서 다시 작성할게요."
    if any(issue.get("code") == "ambiguous_plan_domain" for issue in critical):
        return "운동 플랜인지 식단 플랜인지 먼저 정해야 해서 지금은 확정하지 않을게요. 운동 플랜 작성 또는 식단 플랜 작성 중 하나로 다시 잡아주세요."
    if any(issue.get("code") in {"required_external_retrieval_unavailable", "required_rag_degraded", "semantic_judge_unavailable"} for issue in critical):
        return "프로필 제약이나 전문 근거 확인이 필요한 요청이라 지금 결과는 확정하지 않을게요. 근거를 다시 확인한 뒤 플랜을 작성해야 해요."
    if any(issue.get("code") == "missing_proposed_plan" for issue in critical):
        return "플랜 형태로 정리되지 않아서 바로 반영하지 않을게요. 운동 플랜인지 식단 플랜인지 기준을 잡아 다시 작성할게요."
    if any(issue.get("code") in _PLAN_CONTRACT_CODES for issue in critical):
        return "플랜 저장 형식에 맞지 않는 항목이 있어 바로 반영하지 않을게요. 운동/식단 항목과 날짜를 정리해서 다시 작성할게요."
    return "응답을 안전하게 확정하기 어려운 부분이 있어요. 조건을 한 번만 더 확인한 뒤 다시 작성할게요."
