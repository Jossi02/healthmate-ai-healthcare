"""Search pipeline node for vector and web retrieval."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.core.conversation_state import infer_domain
from app.core.intents import INTENT_CARE, INTENT_INFO, INTENT_MODIFY, INTENT_PLAN
from app.core.profile_constraints import (
    build_profile_constraint_set,
    query_mentions_specialized_topic as _shared_query_mentions_specialized_topic,
    query_needs_evidence as _shared_query_needs_evidence,
    query_needs_user_memory as _shared_query_needs_user_memory,
)
from app.core.prompt_loader import load_prompt
from app.graph.deps import NodeDeps
from app.schemas.llm_responses import QueryRegenResponse, SearchEvalResponse
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)

TOP_K = 8
EXTERNAL_FETCH_TOP_K = 30
_WEB_ENABLED_INTENTS = {INTENT_INFO}
_KNOWN_SEARCH_TARGETS = {"vdb_memory", "vdb_user_important", "vdb_external", "web"}
_ACCEPT_SCORE_BY_INTENT = {
    INTENT_INFO: 0.6,
    INTENT_PLAN: 0.55,
    INTENT_MODIFY: 0.5,
}
_RETRY_SCORE_BY_INTENT = {
    INTENT_INFO: 0.25,
    INTENT_PLAN: 0.25,
    INTENT_MODIFY: 0.0,
}
_MAX_RETRY_BY_INTENT = {
    INTENT_INFO: 0,
    INTENT_PLAN: 0,
    INTENT_MODIFY: 0,
}
_STRICT_FAIL_CLOSED_CONSTRAINTS = {
    "kidney_disease",
    "gout",
    "pregnancy",
    "eating_disorder_risk",
    "extreme_diet_risk",
}
_INFO_WEB_KEYWORDS = (
    "최신",
    "최근",
    "요즘",
    "뉴스",
    "업데이트",
)

_EVAL_SYSTEM_PROMPT = load_prompt("nodes/search/eval.md")
_QUERY_REGEN_PROMPT = load_prompt("nodes/search/query_regen.md")


@dataclass(frozen=True)
class RetrievalSpec:
    should_search: bool
    targets: list[str]
    domain: str
    query_span: str
    topics: list[str]
    use_cases: list[str]
    profile_targets: list[str]
    constraints: list[str]
    critical_constraints: list[str]
    negative_constraints: list[str]
    goals: list[str]
    requires_recency: bool
    strict_filter: dict[str, Any] | None
    relaxed_filter: dict[str, Any] | None


def make_search_node(deps: NodeDeps):
    async def search_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        query = state.get("search_query") or _resolved_query(state)
        targets = _safe_search_targets(state.get("search_targets"))
        retry_count = state.get("search_retry_count", 0)
        intent = state.get("intent", "")
        deps.trace.record_current_event(
            stage="search",
            status="info",
            title="Search started",
            detail={
                "intent": intent,
                "raw_query": query,
                "targets": targets,
                "retry_count": retry_count,
            },
        )

        spec = _build_retrieval_spec(state, query, targets)
        query = spec.query_span
        targets = spec.targets
        deps.trace.record_current_event(
            stage="search",
            status="info",
            title="Retrieval spec prepared",
            detail={
                "spec": _retrieval_spec_trace(spec),
                "strict_filter": spec.strict_filter,
                "relaxed_filter": spec.relaxed_filter,
            },
        )

        if (
            intent in _WEB_ENABLED_INTENTS
            and "vdb_external" in targets
            and "web" not in targets
            and _info_needs_web(_resolved_query(state))
        ):
            targets.append("web")

        if not targets:
            search_quality = "degraded" if spec.should_search else "ok"
            deps.trace.record_current_event(
                stage="search",
                status="warn" if search_quality == "degraded" else "ok",
                title="Search skipped",
                detail={"reason": "no_targets", "search_quality": search_quality, "spec_should_search": spec.should_search},
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {"search_results": [], "search_quality": search_quality}

        try:
            query_vec = await deps.embed.embed(query)
        except Exception as exc:
            logger.error("Embedding failed: %s", exc)
            deps.trace.record_current_alert(
                severity="error",
                message="Embedding failed before search",
                detail={"error": str(exc)},
            )
            return _degraded(state, intent)

        raw_results = await _parallel_search(
            deps,
            state["user_id"],
            query,
            query_vec,
            targets,
            external_filter=spec.strict_filter,
        )
        merged_results = _merge_results(raw_results)
        merged_results = await _expand_external_results_if_needed(
            deps,
            query_vec,
            targets,
            merged_results,
            strict_filter=spec.strict_filter,
            relaxed_filter=spec.relaxed_filter,
        )
        merged_results, post_filter_quality = _post_filter_external_results(merged_results, spec)
        merged_results = _rerank_external_results(merged_results, spec)
        merged_results = merged_results[:TOP_K]
        search_quality = _search_quality_from_results(spec, merged_results, post_filter_quality)
        if _weak_external_should_fail_closed(state, spec, search_quality):
            deps.trace.record_current_alert(
                severity="warning",
                message="Weak external retrieval rejected for strict constrained request",
                detail={
                    "domain": spec.domain,
                    "critical_constraints": spec.critical_constraints,
                    "post_filter_quality": post_filter_quality,
                    "top_results": _preview_results(merged_results),
                },
            )
            return {
                "search_results": [],
                "search_quality": "degraded",
                "search_retry_count": retry_count,
            }

        if _should_skip_eval(state, merged_results):
            deps.trace.record_current_event(
                stage="search",
                status="ok",
                title="Search completed with lightweight policy",
                detail={
                    "results": len(merged_results),
                    "search_quality": search_quality,
                    "reason": _skip_eval_reason(state, merged_results),
                    "top_results": _preview_results(merged_results),
                    "returned_kb_ids": _returned_kb_ids(merged_results),
                },
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {
                "search_results": merged_results,
                "search_quality": search_quality,
                "search_retry_count": retry_count,
            }

        score = await _evaluate(deps, query, merged_results)
        logger.info("Search quality score=%.2f retry=%d", score, retry_count)

        accept_score = _accept_score_for_intent(intent)
        if score >= accept_score:
            deps.trace.record_current_event(
                stage="search",
                status="ok",
                title="Search completed",
                detail={
                    "score": score,
                    "accept_score": accept_score,
                    "search_quality": search_quality,
                    "results": len(merged_results),
                    "top_results": _preview_results(merged_results),
                    "returned_kb_ids": _returned_kb_ids(merged_results),
                },
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {
                "search_results": merged_results,
                "search_quality": search_quality,
                "search_retry_count": retry_count,
            }

        max_retry = _max_retry_for_intent(intent)
        if retry_count >= max_retry:
            deps.trace.record_current_alert(
                severity="warning",
                message="Search degraded after retry limit",
                detail={"score": score, "retry_count": retry_count, "max_retry": max_retry},
            )
            return _degraded(state, intent)

        retry_score = _retry_score_for_intent(intent)
        if score < retry_score:
            new_query = await _regenerate_query(deps, _resolved_query(state), merged_results)
            deps.trace.record_current_event(
                stage="search",
                status="warn",
                title="Search query regenerated",
                detail={"score": score, "retry_score": retry_score, "new_query": new_query},
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {
                "search_query": new_query,
                "search_retry_count": retry_count + 1,
            }

        deps.trace.record_current_event(
            stage="search",
            status="warn",
            title="Search retry requested",
            detail={"score": score, "retry_count": retry_count + 1, "accept_score": accept_score},
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return {"search_retry_count": retry_count + 1}

    return search_node


def _augment_query(query: str, state: GraphState) -> str:
    if state.get("search_query"):
        return query

    profile = state.get("user_profile") or {}
    additions: list[str] = []

    if profile.get("goal"):
        additions.append(f"목표:{profile['goal']}")
    if profile.get("age"):
        additions.append(f"나이:{profile['age']}")
    if profile.get("gender"):
        additions.append(f"성별:{profile['gender']}")
    weight = _profile_weight_value(profile)
    if weight:
        additions.append(f"체중:{weight}kg")
    if profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level"):
        additions.append(
            "운동수준:"
            f"{profile.get('exercise_level') or profile.get('fitness_level') or profile.get('activity_level')}"
        )
    if profile.get("available_time_minutes"):
        additions.append(f"가능시간:{profile['available_time_minutes']}분")
    frequency = _profile_frequency_value(profile)
    if frequency:
        additions.append(f"운동빈도:주{frequency}회")
    orientation = _profile_social_orientation(profile)
    if orientation:
        additions.append(f"운동성향:{'외향형' if orientation == 'extrovert' else '내향형'}")
    if profile.get("diet_goal") or profile.get("diet_type") or profile.get("primary_goal"):
        additions.append(
            "식단/운동목표:"
            f"{profile.get('diet_goal') or profile.get('diet_type') or profile.get('primary_goal')}"
        )
    if any(marker in query for marker in ("운동", "루틴", "유산소", "근력", "workout", "exercise")):
        additions.append("운동구성:스트레칭/유산소/상체/하체")
        goal_text = " ".join(
            str(value)
            for value in (profile.get("goal"), profile.get("diet_goal"), profile.get("primary_goal"))
            if value
        ).lower()
        if orientation == "introvert" and any(
            marker in goal_text for marker in ("fat_loss", "weight_loss", "diet", "다이어트", "감량")
        ):
            additions.append("선호운동:집에서 하는 유산소")
    if profile.get("lifestyle") or profile.get("schedule"):
        additions.append(f"생활패턴:{profile.get('lifestyle') or profile.get('schedule')}")
    if profile.get("injury_history"):
        additions.append(f"부상:{profile['injury_history']}")
    if profile.get("medical_conditions") or profile.get("conditions"):
        additions.append(f"질환:{profile.get('medical_conditions') or profile.get('conditions')}")
    if profile.get("pain_points"):
        additions.append(f"통증:{profile['pain_points']}")
    if profile.get("allergies") or profile.get("dietary_restrictions"):
        additions.append(f"식이제약:{profile.get('allergies') or profile.get('dietary_restrictions')}")

    if not additions:
        return query
    return f"{query} [{', '.join(additions)}]"


def _profile_frequency_value(profile: dict) -> int | None:
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


def _profile_weight_value(profile: dict) -> int | None:
    for key in ("weight", "body_weight", "body_weight_kg", "current_weight_kg"):
        value = profile.get(key)
        if not value:
            continue
        try:
            if isinstance(value, str):
                match = re.search(r"-?\d+(?:\.\d+)?", value)
                if not match:
                    continue
                return int(float(match.group(0)))
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _profile_bmi_value(profile: dict) -> float | None:
    raw_bmi = profile.get("bmi")
    if raw_bmi:
        try:
            if isinstance(raw_bmi, str):
                match = re.search(r"-?\d+(?:\.\d+)?", raw_bmi)
                if not match:
                    return None
                return float(match.group(0))
            return float(raw_bmi)
        except (TypeError, ValueError):
            return None

    weight = _profile_weight_value(profile)
    height = profile.get("height") or profile.get("height_cm") or profile.get("body_height_cm")
    if not weight or not height:
        return None
    try:
        if isinstance(height, str):
            match = re.search(r"-?\d+(?:\.\d+)?", height)
            if not match:
                return None
            height_value = float(match.group(0))
        else:
            height_value = float(height)
    except (TypeError, ValueError):
        return None
    if height_value <= 0:
        return None
    height_m = height_value / 100 if height_value > 3 else height_value
    if height_m <= 0:
        return None
    return round(weight / (height_m * height_m), 1)


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


def _resolved_query(state: GraphState) -> str:
    resolution = state.get("context_resolution") or {}
    resolved_text = str(resolution.get("resolved_text") or "").strip()
    resolved_reference = resolution.get("resolved_reference")
    confidence = _safe_float(resolution.get("confidence"))

    if resolved_reference and resolved_reference != "none" and resolved_text and confidence >= 0.6:
        return resolved_text
    return str(state.get("user_message") or "")


def _normalize_targets(state: GraphState, query: str, targets: list[str]) -> list[str]:
    normalized = _safe_search_targets(targets)
    intent = state.get("intent")
    action_intent = state.get("action_intent")

    if action_intent in {"create", "modify"} or intent == INTENT_MODIFY:
        filtered = [target for target in normalized if target != "web"]
        if not _query_needs_user_memory(query):
            filtered = [target for target in filtered if target not in {"vdb_memory", "vdb_user_important"}]
        if not _plan_needs_external_rag(state, query):
            filtered = [target for target in filtered if target != "vdb_external"]
        return filtered

    if intent == INTENT_INFO and "web" in normalized and not _info_needs_web(query):
        return [target for target in normalized if target != "web"]

    return normalized


def _info_needs_web(query: str) -> bool:
    normalized = query.strip().lower()
    return any(keyword in normalized for keyword in _INFO_WEB_KEYWORDS)


def _apply_rag_trigger_targets(state: GraphState, query: str, targets: list[str]) -> list[str]:
    normalized = _safe_search_targets(targets)
    intent = state.get("intent")
    action_intent = str(state.get("action_intent") or "")

    if intent in {INTENT_PLAN, INTENT_MODIFY} or action_intent in {"create", "modify"}:
        if _plan_needs_external_rag(state, query) and "vdb_external" not in normalized:
            normalized.append("vdb_external")
        if _query_needs_user_memory(query):
            for target in ("vdb_memory", "vdb_user_important"):
                if target not in normalized:
                    normalized.append(target)
        return normalized

    if intent == INTENT_INFO:
        if "vdb_external" not in normalized:
            normalized.append("vdb_external")
        if _info_needs_web(query) and "web" not in normalized:
            normalized.append("web")
        if _query_needs_user_memory(query):
            for target in ("vdb_memory", "vdb_user_important"):
                if target not in normalized:
                    normalized.append(target)
        return normalized

    if intent == INTENT_CARE and state.get("requires_past_memory"):
        for target in ("vdb_memory", "vdb_user_important"):
            if target not in normalized:
                normalized.append(target)

    return normalized


def _plan_needs_external_rag(state: GraphState, query: str) -> bool:
    user_query = str(state.get("user_message") or query)
    profile_constraints = _safe_mapping(state.get("profile_constraints"))
    profile = _safe_mapping(state.get("user_profile"))
    return (
        bool(profile_constraints.get("should_use_rag"))
        or _profile_has_rag_risk(profile)
        or _query_needs_evidence(user_query)
        or _query_mentions_specialized_topic(user_query)
    )


def _query_needs_evidence(query: str) -> bool:
    return _shared_query_needs_evidence(query)


def _query_mentions_specialized_topic(query: str) -> bool:
    return _shared_query_mentions_specialized_topic(query)


def _query_needs_user_memory(query: str) -> bool:
    return _shared_query_needs_user_memory(query)


def _profile_has_rag_risk(profile: dict) -> bool:
    age = _safe_int(profile.get("age"))
    weight = _profile_weight_value(profile)
    bmi = _profile_bmi_value(profile)
    if age is not None and (age < 19 or age >= 60):
        return True
    if weight is not None and weight >= 90:
        return True
    if bmi is not None and bmi >= 25:
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
    )
    if any(_as_text_list(profile.get(field)) for field in risk_fields):
        return True
    if _is_plant_based_profile(profile):
        return True

    profile_text = " ".join(
        str(profile.get(field) or "")
        for field in ("goal", "primary_goal", "diet_goal", "context_notes", "lifestyle", "schedule")
    ).lower()
    return any(
        keyword in profile_text
        for keyword in (
            "diabetes",
            "당뇨",
            "혈당",
            "hypertension",
            "고혈압",
            "heart",
            "심장",
            "심혈관",
            "천식",
            "asthma",
            "arthritis",
            "관절염",
            "비만",
            "bmi",
            "bone",
            "골감소",
        )
    )


def _build_retrieval_spec(state: GraphState, query: str, initial_targets: object) -> RetrievalSpec:
    query_span = _build_query_span(query)
    targets = _apply_rag_trigger_targets(state, query_span, _safe_search_targets(initial_targets))
    targets = _normalize_targets(state, query_span, targets)
    requires_recency = _info_needs_web(query_span)
    domain = _resolved_domain(state, query_span)
    action_intent = str(state.get("action_intent") or "")
    profile = _safe_mapping(state.get("user_profile"))
    topics = _external_topics_for_query(domain, query_span)
    use_cases = _external_use_cases_for_request(domain, action_intent, query_span)
    compiled_constraints = _safe_mapping(state.get("profile_constraints")) or build_profile_constraint_set(
        profile,
        query_span,
        domain=domain,
    )
    profile_targets = _metadata_filter_values(compiled_constraints.get("profile_targets"))
    negative_constraints = _metadata_filter_values(compiled_constraints.get("negative_constraints"))
    if "retrieval_constraints" in compiled_constraints:
        constraints = _metadata_filter_values(compiled_constraints.get("retrieval_constraints"))
    else:
        constraints = _metadata_filter_values(compiled_constraints.get("constraints"))
    goals = _external_goal_values(
        [
            *_metadata_filter_values(compiled_constraints.get("goals")),
            *_external_goals_for_profile_and_query(profile, query_span),
        ]
    )
    if "retrieval_critical_constraints" in compiled_constraints:
        critical_constraints = _metadata_filter_values(compiled_constraints.get("retrieval_critical_constraints"))
    else:
        critical_constraints = _metadata_filter_values(
            compiled_constraints.get("critical_constraints") or _critical_constraints_for_request(constraints)
        )
    strict_filter, relaxed_filter = _build_external_filters_from_parts(
        domain=domain,
        topics=topics,
        use_cases=use_cases,
        profile_targets=profile_targets,
        constraints=constraints,
        goals=goals,
    )
    return RetrievalSpec(
        should_search=bool(targets),
        targets=targets,
        domain=domain,
        query_span=query_span,
        topics=topics,
        use_cases=use_cases,
        profile_targets=profile_targets,
        constraints=constraints,
        critical_constraints=critical_constraints,
        negative_constraints=negative_constraints,
        goals=goals,
        requires_recency=requires_recency,
        strict_filter=strict_filter,
        relaxed_filter=relaxed_filter,
    )


def _build_query_span(query: str) -> str:
    text = str(query or "").strip()
    text = re.sub(r"\s*\[[^\]]+\]\s*$", "", text).strip()
    return text or str(query or "").strip()


def _critical_constraints_for_request(constraints: list[str]) -> list[str]:
    non_critical = {"low_time"}
    return [constraint for constraint in constraints if constraint not in non_critical]


def _retrieval_spec_trace(spec: RetrievalSpec) -> dict[str, Any]:
    payload = asdict(spec)
    payload.pop("strict_filter", None)
    payload.pop("relaxed_filter", None)
    payload["raw_top_k"] = EXTERNAL_FETCH_TOP_K
    payload["final_top_k"] = TOP_K
    return payload


def _build_external_filters(state: GraphState, query: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    spec = _build_retrieval_spec(state, query, list(state.get("search_targets") or []))
    return spec.strict_filter, spec.relaxed_filter


def _build_external_filters_from_parts(
    *,
    domain: str,
    topics: list[str],
    use_cases: list[str],
    profile_targets: list[str],
    constraints: list[str],
    goals: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    topics = _metadata_filter_values(topics)
    use_cases = _metadata_filter_values(use_cases)
    profile_targets = _metadata_filter_values(profile_targets)
    constraints = _metadata_filter_values(constraints)
    goals = _metadata_filter_values(goals)

    clauses: list[dict[str, Any]] = []
    relaxed_filter: dict[str, Any] | None = None

    clauses.append({"source_type": {"$in": ["external_kb"]}})
    if domain in {"workout", "diet", "safety", "habit"}:
        clauses.append({"domain": {"$in": [domain]}})
    if use_cases:
        clauses.append({"use_cases": {"$in": use_cases}})
    if profile_targets:
        clauses.append({"profile_targets": {"$in": profile_targets}})
    if constraints:
        clauses.append({"constraints": {"$in": constraints}})
    if goals:
        clauses.append({"goals": {"$in": goals}})

    if not clauses:
        return None, None
    relaxed_clauses = [{"source_type": {"$in": ["external_kb"]}}]
    if domain in {"workout", "diet", "safety", "habit"}:
        relaxed_clauses.append({"domain": {"$in": [domain]}})
    if use_cases:
        relaxed_clauses.append({"use_cases": {"$in": use_cases}})
    elif topics:
        relaxed_clauses.append({"topic": {"$in": topics}})

    if len(relaxed_clauses) == 1:
        relaxed_filter = relaxed_clauses[0]
    else:
        relaxed_filter = {"$and": relaxed_clauses}

    if len(clauses) == 1:
        return clauses[0], relaxed_filter if relaxed_filter != clauses[0] else None
    return {"$and": clauses}, relaxed_filter


def _rerank_external_results(
    results: list[dict],
    spec_or_state: RetrievalSpec | GraphState,
    query: str | None = None,
) -> list[dict]:
    if not results:
        return results

    if isinstance(spec_or_state, RetrievalSpec):
        spec = spec_or_state
    else:
        spec = _build_retrieval_spec(spec_or_state, str(query or ""), list(spec_or_state.get("search_targets") or []))

    domain = spec.domain
    expected_topics = set(spec.topics)
    specific_topics = expected_topics - {"physical_activity", "meal_planning"}
    expected_targets = set(spec.profile_targets)
    expected_constraints = set(spec.constraints)
    expected_goals = set(spec.goals)

    def match_bonus(result: dict) -> int:
        if result.get("source") != "external" and result.get("source_type") != "external_kb":
            return 0
        bonus = 0
        if domain and result.get("domain") == domain:
            bonus += 20
        result_constraints = set(_metadata_values(result.get("constraints")))
        result_targets = set(_metadata_values(result.get("profile_targets")))
        result_goals = set(_metadata_values(result.get("goals")))
        matched_constraints = len(expected_constraints & result_constraints)
        extra_constraints = len(result_constraints - expected_constraints) if expected_constraints else 0
        bonus += 16 * matched_constraints
        bonus -= 2 * extra_constraints
        bonus += 6 * len(expected_targets & result_targets)
        bonus += 5 * len(expected_goals & result_goals)
        if specific_topics and result.get("topic") in specific_topics:
            bonus += 15
        elif result.get("topic") in expected_topics:
            bonus += 2
        bonus += _evidence_quality_bonus(result)
        bonus += _specificity_bonus(result, expected_constraints)
        return bonus

    decorated = [
        (match_bonus(result), _safe_float(result.get("score")), -index, result)
        for index, result in enumerate(results)
    ]
    return [result for *_unused, result in sorted(decorated, reverse=True)]


def _post_filter_external_results(results: list[dict], spec: RetrievalSpec) -> tuple[list[dict], str]:
    if "vdb_external" not in spec.targets:
        return results, "ok"

    negative = set(spec.negative_constraints)
    critical = set(spec.critical_constraints)
    non_external = [
        result
        for result in results
        if result.get("source") != "external" and result.get("source_type") != "external_kb"
    ]
    external = [
        result
        for result in results
        if result.get("source") == "external" or result.get("source_type") == "external_kb"
    ]
    if not external:
        return results, "degraded"

    without_negative = [
        result
        for result in external
        if not (negative & set(_metadata_values(result.get("constraints"))))
    ]
    if not without_negative:
        return non_external, "weak"

    if not critical:
        return _merge_results(non_external + without_negative), "ok"

    critical_matches = [
        result
        for result in without_negative
        if critical.issubset(set(_metadata_values(result.get("constraints"))))
    ]
    if critical_matches:
        return _merge_results(non_external + critical_matches), "ok"
    return _merge_results(non_external + without_negative), "weak"


def _search_quality_from_results(spec: RetrievalSpec, results: list[dict], post_filter_quality: str) -> str:
    if not spec.should_search:
        return "ok"
    if not results:
        return "degraded"
    if post_filter_quality in {"weak", "degraded"}:
        return post_filter_quality
    if "vdb_external" not in spec.targets:
        return "ok"

    external = [
        result
        for result in results
        if result.get("source") == "external" or result.get("source_type") == "external_kb"
    ]
    if not external:
        return "degraded"
    if spec.domain in {"workout", "diet"} and not any(result.get("domain") == spec.domain for result in external):
        return "weak"
    critical = set(spec.critical_constraints)
    if critical and not any(critical.issubset(set(_metadata_values(result.get("constraints")))) for result in external):
        return "weak"
    return "ok"


def _weak_external_should_fail_closed(state: GraphState, spec: RetrievalSpec, search_quality: str) -> bool:
    if search_quality != "weak":
        return False
    if "vdb_external" not in spec.targets:
        return False
    action_intent = str(state.get("action_intent") or "")
    if action_intent not in {"create", "modify"}:
        return False
    strict_constraints = set(spec.critical_constraints) & _STRICT_FAIL_CLOSED_CONSTRAINTS
    return bool(strict_constraints)


def _evidence_quality_bonus(result: dict) -> int:
    bonus = 0
    try:
        rank = int(float(result.get("evidence_rank") or 0))
    except (TypeError, ValueError):
        rank = 0
    bonus += min(max(rank, 0), 5)
    try:
        year = int(float(result.get("year") or 0))
    except (TypeError, ValueError):
        year = 0
    if year >= 2024:
        bonus += 2
    elif year and year < 2015:
        bonus -= 1
    if result.get("risk_level") in {"avoid", "caution"}:
        bonus += 1
    return bonus


def _specificity_bonus(result: dict, expected_constraints: set[str]) -> int:
    constraints = set(_metadata_values(result.get("constraints")))
    if not constraints:
        return 0
    if expected_constraints and expected_constraints.issubset(constraints):
        if len(constraints) <= max(len(expected_constraints) + 2, 3):
            return 6
        return -2
    if len(constraints) >= 6:
        return -4
    return 0


def _metadata_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _metadata_filter_values(value: object, *, limit: int = 40, item_limit: int = 80) -> list[str]:
    values: list[str] = []
    for item in _metadata_values(value):
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if len(text) > item_limit:
            text = text[:item_limit].rstrip()
        if text not in values:
            values.append(text)
        if len(values) >= limit:
            break
    return values


def _safe_search_targets(value: object) -> list[str]:
    if value is None:
        raw_values: list[object] = []
    elif isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []

    targets: list[str] = []
    for item in raw_values:
        target = str(item or "").strip()
        if target in _KNOWN_SEARCH_TARGETS and target not in targets:
            targets.append(target)
    return targets


def _safe_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _returned_kb_ids(results: list[dict]) -> list[str]:
    ids: list[str] = []
    for result in results:
        kb_id = str(result.get("kb_id") or (result.get("metadata") or {}).get("kb_id") or "")
        if kb_id:
            ids.append(kb_id)
    return ids


def _resolved_domain(state: GraphState, query: str) -> str:
    domain = str(state.get("domain") or "").strip()
    if domain in {"workout", "diet", "profile"}:
        return domain
    resolution = state.get("context_resolution") or {}
    resolved_domain = str(resolution.get("resolved_domain") or "").strip()
    if resolved_domain in {"workout", "diet", "profile"} and resolved_domain != "none":
        return resolved_domain
    return infer_domain(query)


def _external_categories_for_domain(domain: str) -> list[str]:
    if domain == "workout":
        return [
            "workout_resistance_guidelines",
            "workout_technique",
            "workout_program_design",
            "hypertrophy_volume",
            "hypertrophy_frequency",
            "cardio_guidelines",
            "hiit_programming",
            "hiit_efficiency",
            "mobility_pnf",
            "stretching_performance",
        ]
    if domain == "diet":
        return [
            "nutrition_kdri",
            "nutrition_protein",
            "nutrition_timing",
            "nutrition_allergy",
            "supplement_creatine",
            "supplement_omega3",
            "physique_cutting",
        ]
    return []


def _external_topics_for_query(domain: str, query: str) -> list[str]:
    normalized = query.lower()
    topics: list[str] = []
    if domain == "workout":
        if any(keyword in normalized for keyword in ("유산소", "심박", "cardio", "걷기", "산책")):
            topics.append("cardio")
        if any(keyword in normalized for keyword in ("근력", "근비대", "상체", "하체", "세트", "hypertrophy")):
            topics.append("resistance_training")
        if any(keyword in normalized for keyword in ("스트레칭", "가동성", "mobility", "pnf")):
            topics.append("mobility")
        if any(keyword in normalized for keyword in ("hiit", "인터벌")):
            topics.append("hiit")
        topics.append("physical_activity")
    elif domain == "diet":
        if any(keyword in normalized for keyword in ("단백질", "protein", "근육", "근비대")):
            topics.append("protein")
        if any(keyword in normalized for keyword in ("알레르기", "유당", "우유", "계란", "견과", "갑각류")):
            topics.append("food_allergy")
        if any(keyword in normalized for keyword in ("혈당", "당뇨", "diabetes")):
            topics.append("diabetes_nutrition")
        if any(keyword in normalized for keyword in ("고혈압", "혈압", "나트륨", "dash")):
            topics.append("heart_health_nutrition")
        if any(keyword in normalized for keyword in ("보충제", "크레아틴", "오메가")):
            topics.append("supplement")
        topics.append("meal_planning")
    return list(dict.fromkeys(topics))


def _external_use_cases_for_request(domain: str, action_intent: str, query: str) -> list[str]:
    normalized = query.lower()
    if domain == "workout":
        use_cases = {
            "create": [
                "plan_create",
                "program_design",
                "novice_programming",
                "intermediate_programming",
                "cardio_programming",
                "mobility",
                "coaching",
            ],
            "modify": [
                "plan_modify",
                "risk_repair",
                "program_adjustment",
                "fatigue_management",
                "injury_prevention",
                "risk_screening",
                "mobility",
                "coaching",
            ],
            "info": [
                "info_answer",
                "program_design",
                "technique_cueing",
                "evidence_interpretation",
                "injury_prevention",
                "risk_screening",
                "mobility",
                "coaching",
            ],
        }.get(action_intent, ["info_answer", "program_design", "coaching", "evidence_interpretation"])
        if any(keyword in normalized for keyword in ("통증", "부상", "아픔", "무릎", "허리", "어깨")):
            use_cases.extend(["injury_prevention", "risk_screening", "mobility"])
        if any(keyword in normalized for keyword in ("근비대", "근육", "상체", "하체", "세트")):
            use_cases.extend(["hypertrophy_programming", "strength_programming"])
        if any(keyword in normalized for keyword in ("hiit", "인터벌")):
            use_cases.extend(["hiit_programming", "cardio_programming"])
        return list(dict.fromkeys(use_cases))

    if domain == "diet":
        use_cases = {
            "create": [
                "plan_create",
                "meal_planning",
                "training_day_nutrition",
                "muscle_gain",
                "fat_loss",
                "coaching",
            ],
            "modify": [
                "plan_modify",
                "risk_repair",
                "meal_planning",
                "fat_loss",
                "allergy_safe_planning",
                "coaching",
            ],
            "info": [
                "info_answer",
                "meal_planning",
                "training_day_nutrition",
                "supplement_use",
                "allergy_safe_planning",
                "evidence_interpretation",
                "coaching",
            ],
        }.get(action_intent, ["info_answer", "meal_planning", "coaching", "evidence_interpretation"])
        if any(keyword in normalized for keyword in ("알레르기", "유당", "갑각류", "계란", "우유", "견과")):
            use_cases.append("allergy_safe_planning")
        if any(keyword in normalized for keyword in ("보충제", "크레아틴", "오메가3")):
            use_cases.append("supplement_use")
        if any(keyword in normalized for keyword in ("혈당", "당뇨", "diabetes")):
            use_cases.append("glucose_control")
        if any(keyword in normalized for keyword in ("고혈압", "혈압", "나트륨", "dash")):
            use_cases.append("heart_health")
        return list(dict.fromkeys(use_cases))

    return []


def _external_profile_targets_for_profile(profile: dict) -> list[str]:
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

    weight = _profile_weight_value(profile)
    bmi = _profile_bmi_value(profile)
    if weight is not None and weight >= 90:
        targets.append("high_weight")
    if bmi is not None and bmi >= 25:
        targets.append("high_weight")

    if _as_text_list(profile.get("allergies")) or _as_text_list(profile.get("allergy")):
        targets.append("food_allergy")
    if _is_plant_based_profile(profile):
        targets.append("plant_based")
    return list(dict.fromkeys(targets))


def _external_constraints_for_profile_and_query(profile: dict, query: str) -> list[str]:
    values: list[str] = []
    for field in (
        "injury_history",
        "medical_history",
        "medical_conditions",
        "conditions",
        "pain_points",
        "allergies",
        "allergy",
        "diet_type",
        "diet_goal",
        "dietary_restrictions",
        "goal",
        "context_notes",
    ):
        values.extend(_as_text_list(profile.get(field)))
    text = " ".join(values).lower()
    text = f"{text} {query.lower()}"
    text = _strip_negated_constraint_mentions(text)
    constraints: list[str] = []
    markers = {
        "knee_pain": ("무릎", "knee"),
        "back_pain": ("허리", "요통", "back", "sciatica"),
        "shoulder_pain": ("어깨", "shoulder"),
        "wrist_pain": ("손목", "wrist"),
        "ankle_pain": ("발목", "ankle"),
        "hypertension": ("고혈압", "혈압", "hypertension"),
        "diabetes": ("당뇨", "혈당", "diabetes", "glucose"),
        "cardiovascular_disease": ("심혈관", "심장", "협심증", "cardiovascular", "heart disease"),
        "asthma": ("천식", "asthma"),
        "kidney_disease": ("신장질환", "신장 질환", "콩팥", "만성신부전", "ckd", "kidney disease", "renal", "kidney", "renal disease"),
        "gout": ("통풍", "요산", "gout", "uric acid", "hyperuricemia"),
        "pregnancy": ("임신", "임산부", "pregnant", "pregnancy", "prenatal"),
        "eating_disorder_risk": ("폭식", "절식", "섭식장애", "섭식 장애", "eating disorder", "binge", "purge", "omad", "detox", "cleanse"),
        "arthritis": ("관절염", "arthritis"),
        "food_allergy": ("알레르기", "allergy", "유당", "유제품", "우유", "계란", "달걀", "견과", "갑각류", "밀", "대두"),
        "dairy_allergy": ("유당", "유제품", "우유", "milk", "dairy"),
        "egg_allergy": ("계란", "달걀", "egg"),
        "nut_allergy": ("견과", "땅콩", "peanut", "nut"),
        "shellfish_allergy": ("갑각류", "새우", "shellfish", "shrimp"),
        "wheat_allergy": ("밀", "wheat", "gluten"),
        "soy_allergy": ("대두", "soy"),
        "vegetarian": ("채식", "vegetarian"),
        "vegan": ("비건", "vegan"),
        "obesity": ("비만", "bmi", "체질량", "obesity"),
        "low_time": ("바빠", "시간", "8분", "10분", "15분", "짧"),
        "extreme_diet_risk": ("900kcal", "굶", "단식", "일주일에 7kg", "극단", "800kcal", "very low calorie", "one meal a day", "omad", "detox", "cleanse"),
    }
    for constraint, keywords in markers.items():
        if any(keyword in text for keyword in keywords):
            constraints.append(constraint)
    bmi = _profile_bmi_value(profile)
    if bmi is not None and bmi >= 25:
        constraints.append("obesity")
    return list(dict.fromkeys(constraints))


def _external_negative_constraints_for_profile_and_query(profile: dict, query: str) -> list[str]:
    values: list[str] = []
    for field in (
        "injury_history",
        "medical_history",
        "medical_conditions",
        "conditions",
        "pain_points",
        "allergies",
        "allergy",
        "dietary_restrictions",
        "context_notes",
    ):
        values.extend(_as_text_list(profile.get(field)))
    text = " ".join(values).lower()
    text = f"{text} {query.lower()}"
    negatives: list[str] = []
    for constraint, pattern in _negated_constraint_patterns():
        if re.search(pattern, text, flags=re.IGNORECASE):
            negatives.append(constraint)
    return list(dict.fromkeys(negatives))


def _strip_negated_constraint_mentions(text: str) -> str:
    cleaned = text
    for _constraint, pattern in _negated_constraint_patterns():
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _negated_constraint_patterns() -> tuple[tuple[str, str], ...]:
    none_words = r"(?:해당\s*없음|해당없음|없음|없어|없어요|아님|아니야|아니에요|없고|없지만)"

    def pattern(words: str, suffix: str = "") -> str:
        return rf"(?:{words})(?:\s*{suffix})?\s*(?:은|는|이|가|도)?\s*{none_words}"

    return (
        ("hypertension", pattern(r"고혈압|혈압|hypertension")),
        ("diabetes", pattern(r"당뇨|혈당|diabetes|glucose")),
        ("cardiovascular_disease", pattern(r"심혈관|심장|협심증|cardiovascular|heart disease")),
        ("asthma", pattern(r"천식|asthma")),
        ("kidney_disease", pattern(r"신장질환|신장\s*질환|콩팥|만성신부전|ckd|kidney disease|renal|kidney|renal disease")),
        ("gout", pattern(r"통풍|요산|gout|uric acid|hyperuricemia")),
        ("pregnancy", pattern(r"임신|임산부|pregnant|pregnancy|prenatal")),
        ("eating_disorder_risk", pattern(r"폭식|절식|섭식장애|섭식\s*장애|eating disorder|binge|purge|omad|detox|cleanse")),
        ("arthritis", pattern(r"관절염|arthritis")),
        ("knee_pain", pattern(r"무릎|knee", r"(?:통증|부상|pain)?")),
        ("back_pain", pattern(r"허리|요통|back|sciatica", r"(?:통증|부상|pain)?")),
        ("shoulder_pain", pattern(r"어깨|shoulder", r"(?:통증|부상|pain)?")),
        ("wrist_pain", pattern(r"손목|wrist", r"(?:통증|부상|pain)?")),
        ("ankle_pain", pattern(r"발목|ankle", r"(?:통증|부상|pain)?")),
        ("dairy_allergy", pattern(r"유제품|유당|우유|dairy|milk", r"알레르기?")),
        ("egg_allergy", pattern(r"달걀|계란|egg", r"알레르기?")),
        ("nut_allergy", pattern(r"견과류|견과|땅콩|nut|peanut", r"알레르기?")),
        ("shellfish_allergy", pattern(r"갑각류|새우|shellfish|shrimp", r"알레르기?")),
        ("wheat_allergy", pattern(r"밀|wheat|gluten", r"알레르기?")),
        ("soy_allergy", pattern(r"대두|soy", r"알레르기?")),
    )


def _external_goals_for_profile_and_query(profile: dict, query: str) -> list[str]:
    text = " ".join(
        str(profile.get(field) or "")
        for field in ("goal", "primary_goal", "diet_goal", "context_notes", "lifestyle")
    ).lower()
    text = f"{text} {query.lower()}"
    goals: list[str] = []
    markers = {
        "fat_loss": ("fat_loss", "weight_loss", "다이어트", "감량", "체중"),
        "muscle_gain": ("muscle", "strength", "근육", "근비대", "근력"),
        "mobility": ("mobility", "가동성", "스트레칭", "유연성", "관절"),
        "glucose_control": ("glucose", "diabetes", "혈당", "당뇨"),
        "heart_health": ("heart", "cardio", "혈압", "고혈압", "심장", "심혈관", "건강 유지"),
        "habit": ("habit", "consistency", "습관", "꾸준", "건강 유지", "건강"),
    }
    for goal, keywords in markers.items():
        if any(keyword in text for keyword in keywords):
            goals.append(goal)
    return list(dict.fromkeys(goals))


def _external_goal_values(goals: list[str]) -> list[str]:
    aliases = {
        "weight_loss": "fat_loss",
        "weight management": "fat_loss",
        "weight_management": "fat_loss",
    }
    allowed = {"bone_health", "fat_loss", "glucose_control", "habit", "heart_health", "mobility", "muscle_gain"}
    normalized: list[str] = []
    for goal in goals:
        value = aliases.get(str(goal).strip().lower(), str(goal).strip().lower())
        if value in allowed:
            normalized.append(value)
    return list(dict.fromkeys(normalized))


def _external_populations_for_profile(state: GraphState) -> list[str]:
    profile = state.get("user_profile") or {}
    populations: list[str] = []
    age = profile.get("age")
    if isinstance(age, (int, float)) and age >= 65:
        populations.append("older_adults")
    level = str(profile.get("exercise_level") or profile.get("fitness_level") or profile.get("activity_level") or "").lower()
    if any(keyword in level for keyword in ("beginner", "초보", "low", "낮")):
        populations.append("novice")

    allergy_value = profile.get("allergies") or profile.get("allergy") or []
    if isinstance(allergy_value, list):
        allergy_text = " ".join(str(item) for item in allergy_value).lower()
    else:
        allergy_text = str(allergy_value).lower()
    if allergy_text:
        populations.append("food_allergy")

    return list(dict.fromkeys(populations))


def _should_skip_eval(state: GraphState, results: list[dict]) -> bool:
    if not results:
        return False
    intent = state.get("intent")

    if intent == INTENT_MODIFY:
        modify_context = state.get("modify_plan_context") or {}
        items = modify_context.get("items")
        return isinstance(items, list) and len(items) > 0

    if intent == INTENT_INFO:
        return _has_sufficient_info_results(results)

    return False


def _skip_eval_reason(state: GraphState, results: list[dict]) -> str:
    intent = state.get("intent")
    if intent == INTENT_MODIFY:
        return "modify_context_present"
    if intent == INTENT_INFO and _has_sufficient_info_results(results):
        return "info_results_sufficient"
    return "none"


def _has_sufficient_info_results(results: list[dict]) -> bool:
    strong_results = [
        result
        for result in results
        if _safe_float(result.get("score")) >= 0.55
        and len(str(result.get("text") or "")) >= 80
        and result.get("source") in {"external", "web", "important"}
    ]
    return len(strong_results) >= 2


def _accept_score_for_intent(intent: str) -> float:
    return _ACCEPT_SCORE_BY_INTENT.get(intent, 0.6)


def _retry_score_for_intent(intent: str) -> float:
    return _RETRY_SCORE_BY_INTENT.get(intent, 0.25)


def _max_retry_for_intent(intent: str) -> int:
    return _MAX_RETRY_BY_INTENT.get(intent, 0)


async def _parallel_search(
    deps: NodeDeps,
    user_id: str,
    query: str,
    vector: list[float],
    targets: list[str],
    *,
    external_filter: dict[str, Any] | None = None,
) -> list[dict]:
    tasks = []
    for target in targets:
        if target == "vdb_memory":
            tasks.append(deps.pinecone.search_memory(user_id, vector, TOP_K))
        elif target == "vdb_user_important":
            tasks.append(deps.pinecone.search_important(user_id, vector, TOP_K))
        elif target == "vdb_external":
            tasks.append(deps.pinecone.search_external(vector, EXTERNAL_FETCH_TOP_K, metadata_filter=external_filter))
        elif target == "web":
            tasks.append(_web_search(deps, query))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    combined: list[dict] = []
    for result in results:
        if isinstance(result, list):
            combined.extend(result)
        else:
            logger.warning("Search target failed and was ignored: %s", result)
    return combined


async def _expand_external_results_if_needed(
    deps: NodeDeps,
    vector: list[float],
    targets: list[str],
    merged_results: list[dict],
    *,
    strict_filter: dict[str, Any] | None,
    relaxed_filter: dict[str, Any] | None,
) -> list[dict]:
    if "vdb_external" not in targets:
        return merged_results

    external_results = [result for result in merged_results if result.get("source") == "external"]
    if len(external_results) >= 2:
        return merged_results

    expanded_results = list(merged_results)
    if relaxed_filter and relaxed_filter != strict_filter:
        relaxed_results = await deps.pinecone.search_external(
            vector,
            EXTERNAL_FETCH_TOP_K,
            metadata_filter=relaxed_filter,
        )
        expanded_results = _merge_results(expanded_results + relaxed_results)
        external_results = [result for result in expanded_results if result.get("source") == "external"]
        if len(external_results) >= 2:
            return expanded_results

    if strict_filter:
        semantic_results = await deps.pinecone.search_external(vector, EXTERNAL_FETCH_TOP_K)
        expanded_results = _merge_results(expanded_results + semantic_results)

    return expanded_results


async def _web_search(deps: NodeDeps, query: str) -> list[dict]:
    try:
        return await deps.router.search_web(query, max_results=min(TOP_K, 5))
    except Exception as exc:
        logger.warning("Web search failed and was ignored: %s", exc)
        return []


def _merge_results(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []

    for result in sorted(results, key=lambda item: item.get("score", 0.0), reverse=True):
        text = result.get("text", "")
        if text and text not in seen:
            seen.add(text)
            unique.append(result)

    return unique


def _preview_results(results: list[dict], *, limit: int = 5) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for result in results[:limit]:
        preview.append(
            {
                "id": result.get("id"),
                "kb_id": result.get("kb_id") or (result.get("metadata") or {}).get("kb_id"),
                "source": result.get("source"),
                "source_title": result.get("source_title"),
                "score": result.get("score"),
                "metadata": {
                    "domain": result.get("domain"),
                    "topic": result.get("topic"),
                    "category": result.get("category"),
                    "use_cases": result.get("use_cases") or result.get("use_case"),
                    "profile_targets": result.get("profile_targets"),
                    "constraints": result.get("constraints"),
                    "goals": result.get("goals"),
                    "risk_level": result.get("risk_level"),
                    "evidence_type": result.get("evidence_type"),
                    "year": result.get("year"),
                    "url": result.get("url"),
                },
                "text": str(result.get("text") or "")[:240],
            }
        )
    return preview


async def _evaluate(deps: NodeDeps, query: str, results: list[dict]) -> float:
    if not results:
        return 0.0

    snippets = "\n".join(
        f"[{result.get('source', 'unknown')}] {result.get('text', '')[:200]}"
        for result in results[:5]
    )
    user_content = f"질문: {query}\n\n검색 결과:\n{snippets}"

    try:
        raw = await deps.router.generate(
            system_prompt=_EVAL_SYSTEM_PROMPT,
            user_content=user_content,
            response_schema=SearchEvalResponse,
        )
        evaluation = SearchEvalResponse.model_validate_json(raw)
        return evaluation.score
    except Exception as exc:
        logger.warning("Search evaluation failed, defaulting to 0.5: %s", exc)
        return 0.5


async def _regenerate_query(deps: NodeDeps, original: str, results: list[dict]) -> str:
    snippets = "\n".join(result.get("text", "")[:100] for result in results[:3])
    user_content = f"원래 질문: {original}\n\n부실했던 검색 결과:\n{snippets}"

    try:
        raw = await deps.router.generate(
            system_prompt=_QUERY_REGEN_PROMPT,
            user_content=user_content,
            response_schema=QueryRegenResponse,
        )
        regenerated = QueryRegenResponse.model_validate_json(raw)
        return regenerated.query
    except Exception:
        return original


def _degraded(state: GraphState, intent: str) -> dict:
    if intent == INTENT_CARE:
        return {
            "search_results": [],
            "search_quality": "degraded",
            "requires_past_memory": False,
        }

    return {
        "search_results": state.get("search_results") or [],
        "search_quality": "degraded",
    }


def _as_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [text for item in value if (text := _meaningful_profile_text(item))]
    if isinstance(value, tuple):
        return [text for item in value if (text := _meaningful_profile_text(item))]
    text = _meaningful_profile_text(value)
    return [text] if text else []


def _meaningful_profile_text(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    normalized = re.sub(r"\s+", "", text).lower()
    if normalized in {
        "없음",
        "해당없음",
        "해당사항없음",
        "없다",
        "없어요",
        "무",
        "none",
        "no",
        "n/a",
        "na",
        "null",
        "[]",
    }:
        return ""
    return text


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return int(float(match.group(0)))
    except ValueError:
        return None


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return default
        try:
            return float(match.group(0))
        except ValueError:
            return default


def _is_plant_based_profile(profile: dict) -> bool:
    text = " ".join(
        str(profile.get(field) or "")
        for field in ("diet_type", "diet_goal", "dietary_restrictions", "context_notes", "lifestyle")
    ).lower()
    return any(keyword in text for keyword in ("vegan", "vegetarian", "plant", "비건", "채식"))
