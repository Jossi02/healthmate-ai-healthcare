"""Deterministic answer/profile-fit validation before finalization."""
from __future__ import annotations

import json
import time
import re
from typing import Any

from app.core.profile_constraints import as_text_list, profile_bmi_value, profile_weight_value
from app.graph.deps import NodeDeps
from app.schemas.llm_responses import AnswerValidationJudgeResponse
from app.schemas.state import GraphState

INTENT_PLAN = "계획"
INTENT_MODIFY = "수정"
INTENT_APPROVAL = "계획_승인"

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
_HIGH_IMPACT_TERMS = ("점프", "버피", "마운틴클라이머", "전력질주", "sprint", "jump", "burpee")
_BACK_LOAD_TERMS = ("데드리프트", "굿모닝", "무거운 스쿼트", "deadlift", "heavy squat")
_ADVANCED_WORKOUT_TERMS = ("hiit", "인터벌", "전력질주", "고강도", "고중량", "최대", "max", "5세트", "6세트")
_LONG_WORKOUT_TERMS = ("60분", "70분", "80분", "90분", "1시간")
_SODIUM_HEAVY_TERMS = ("라면", "햄", "소시지", "베이컨", "짠", "나트륨", "젓갈", "국물")
_SUGAR_HEAVY_TERMS = ("설탕", "시럽", "탄산", "주스", "디저트", "케이크", "과자", "달콤")
_MEAT_TERMS = ("닭가슴살", "닭고기", "소고기", "돼지고기", "고기", "햄", "베이컨", "연어", "참치", "생선")
_VEGAN_CONFLICT_TERMS = (*_MEAT_TERMS, "계란", "달걀", "우유", "치즈", "요거트", "유제품")

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


def _validate_state(state: GraphState) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    response = str(state.get("response") or "").strip()
    intent = str(state.get("intent") or "")
    action_intent = str(state.get("action_intent") or "")
    proposed_plan = list(state.get("proposed_plan") or [])
    proposed_plan_type = state.get("proposed_plan_type")
    profile_constraints = state.get("profile_constraints") or {}
    retrieval_decision = state.get("retrieval_decision") or {}

    if state.get("request_kind") == "home_recommendation":
        return _report(True, issues, False)

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
    if not _should_run_semantic_validation(state):
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
            detail={"error": str(exc)},
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
                "detail": {"source": "semantic_judge"},
            }
        )

    report = _report(
        not any(issue["severity"] == "critical" for issue in issues),
        issues,
        any(issue.get("retry") for issue in issues),
    )
    deps.trace.record_current_event(
        stage="answer_validator.semantic_judge",
        status="ok" if report["passed"] else "warn",
        title="Semantic validation completed",
        detail={
            "passed": report["passed"],
            "judge_passed": judged.passed,
            "issue_count": len(issues),
            "issues": issues[:4],
        },
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )
    return report


def _should_run_semantic_validation(state: GraphState) -> bool:
    if state.get("request_kind") == "home_recommendation":
        return False
    if not str(state.get("response") or "").strip():
        return False
    proposed_plan = state.get("proposed_plan") or []
    action_intent = state.get("action_intent")
    if action_intent in {"create", "modify"}:
        # Demo behavior: deterministic validators own blocking decisions for
        # structured plan proposals. The LLM semantic judge is useful for
        # observability, but in production-like deploys it can over-block valid
        # profile-safe fallback plans when RAG is weak or profile metadata is rich.
        return False
    retrieval_decision = state.get("retrieval_decision") or {}
    profile_constraints = state.get("profile_constraints") or {}
    field_coverage = profile_constraints.get("profile_field_coverage") or {}
    rich_profile = int(field_coverage.get("present_count") or 0) >= 4
    high_risk_or_constrained = bool(
        profile_constraints.get("safety_risks")
        or profile_constraints.get("critical_constraints")
        or profile_constraints.get("hard_profile_constraints")
        or profile_constraints.get("request_hard_constraints")
    )
    return bool(
        (proposed_plan and (rich_profile or high_risk_or_constrained))
        or (action_intent in {"create", "modify"} and high_risk_or_constrained)
        or retrieval_decision.get("requires_external")
    )


def _semantic_validation_strict_required(state: GraphState) -> bool:
    profile_constraints = state.get("profile_constraints") or {}
    return bool(
        _should_run_semantic_validation(state)
        and (
            _requires_external_fail_closed(state, profile_constraints)
            or profile_constraints.get("safety_risks")
            or profile_constraints.get("critical_constraints")
        )
    )


def _semantic_validation_payload(state: GraphState) -> str:
    payload = {
        "user_message": state.get("user_message"),
        "intent": state.get("intent"),
        "action_intent": state.get("action_intent"),
        "domain": state.get("domain"),
        "profile_summary": (state.get("profile_constraints") or {}).get("summary") or {},
        "hard_profile_constraints": (state.get("profile_constraints") or {}).get("hard_profile_constraints") or [],
        "request_hard_constraints": (state.get("profile_constraints") or {}).get("request_hard_constraints") or [],
        "goals": (state.get("profile_constraints") or {}).get("goals") or [],
        "retrieval_decision": state.get("retrieval_decision") or {},
        "search_quality": state.get("search_quality"),
        "search_result_count": len(state.get("search_results") or []),
        "search_results_preview": _search_results_for_judge(state.get("search_results") or []),
        "retrieval_evidence_contract": _retrieval_evidence_contract(state),
        "proposed_plan_type": state.get("proposed_plan_type"),
        "proposed_plan": _plan_for_judge(state.get("proposed_plan") or []),
        "generation_quality_flags": state.get("generation_quality_flags") or {},
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
        metadata = result.get("metadata") or {}
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
    results = state.get("search_results") or []
    profile_constraints = state.get("profile_constraints") or {}
    kb_ids: list[str] = []
    returned_constraints: set[str] = set()
    for result in results[:8]:
        metadata = result.get("metadata") or {}
        kb_id = str(result.get("kb_id") or metadata.get("kb_id") or "").strip()
        if kb_id:
            kb_ids.append(kb_id)
        values = result.get("constraints") or metadata.get("constraints") or []
        if isinstance(values, str):
            returned_constraints.add(values)
        elif isinstance(values, list):
            returned_constraints.update(str(value) for value in values if value)
    return {
        "requires_external": bool((state.get("retrieval_decision") or {}).get("requires_external")),
        "returned_kb_ids": kb_ids,
        "expected_critical_constraints": profile_constraints.get("retrieval_critical_constraints") or [],
        "returned_constraints": sorted(returned_constraints),
        "grounding_summary": (state.get("draft_components") or {}).get("search_grounding_summary") or "",
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
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


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
    if _has_mixed_workout_diet_plan(proposed_plan) and (
        state.get("domain") == "general"
        or state.get("action_intent") == "approval"
        or ((state.get("context_resolution") or {}).get("resolved_reference") == "active_proposal")
    ):
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
    retrieval_decision = state.get("retrieval_decision") or {}
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

    search_results = state.get("search_results") or []
    draft_components = state.get("draft_components") or {}
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
        kb_id = str(result.get("kb_id") or (result.get("metadata") or {}).get("kb_id") or "").strip()
        if kb_id:
            returned_kb_ids.append(kb_id)
        values = result.get("constraints") or (result.get("metadata") or {}).get("constraints") or []
        if isinstance(values, str):
            returned_constraints.add(values)
        elif isinstance(values, list):
            returned_constraints.update(str(value) for value in values if value)

    expected_constraints = set(profile_constraints.get("retrieval_critical_constraints") or [])
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

    goals = set(profile_constraints.get("goals") or [])
    if proposed_plan_type == "workout" and "muscle_gain" in goals and not _contains_strength_signal(proposed_plan):
        _issue(
            issues,
            "warning",
            "muscle_goal_without_strength_signal",
            "근육 증가 목표인데 근력 운동 신호가 약합니다.",
            retry=False,
        )
    if proposed_plan_type == "diet" and "weight_loss" in goals and not _contains_diet_structure(proposed_plan):
        _issue(
            issues,
            "warning",
            "weight_loss_goal_without_meal_structure",
            "감량 목표인데 식사 구성/칼로리 신호가 약합니다.",
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


def _contains_diet_structure(plan: list[dict]) -> bool:
    text = _plan_text(plan).lower()
    return any(keyword in text for keyword in ("kcal", "칼로리", "단백질", "채소", "현미", "닭가슴살", "두부", "식사", "아침", "점심", "저녁"))


def _requires_external_fail_closed(state: GraphState, profile_constraints: dict[str, Any]) -> bool:
    retrieval_decision = state.get("retrieval_decision") or {}
    if not retrieval_decision.get("requires_external") and not profile_constraints.get("should_use_rag"):
        return False
    action_intent = state.get("action_intent")
    if action_intent in {"create", "modify"}:
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
    return dict(state.get("effective_user_profile") or state.get("user_profile") or {})


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
    "vegetarian_conflict",
    "vegan_conflict",
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
    issues = report.get("issues") or []
    critical_codes = {
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    }
    retrieval_decision = state.get("retrieval_decision") or {}
    profile_constraints = state.get("profile_constraints") or {}
    search_results = state.get("search_results") or []
    grounding_summary = str((state.get("draft_components") or {}).get("search_grounding_summary") or "").strip()
    requires_external = bool(retrieval_decision.get("requires_external") or profile_constraints.get("should_use_rag"))
    return {
        "profile_fit_passed": not bool(critical_codes & _PROFILE_FIT_CODES),
        "plan_write_contract_passed": not bool(critical_codes & _PLAN_CONTRACT_CODES),
        "retrieval_hit": (not requires_external) or bool(search_results),
        "evidence_used": (not requires_external) or bool(grounding_summary or search_results),
        "semantic_judge": report.get("semantic_judge") or {"passed": None, "issue_count": 0},
        "requires_external": requires_external,
        "search_quality": state.get("search_quality"),
        "profile_field_coverage": profile_constraints.get("profile_field_coverage") or {},
    }


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
