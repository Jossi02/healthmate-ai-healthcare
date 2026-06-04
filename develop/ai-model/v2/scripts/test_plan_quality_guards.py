from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.conversation_state import (
    active_proposal_is_mixed_domain,
    append_recent_turn,
    build_active_proposal,
    build_recent_turn,
    canonical_profile_context,
    evolve_active_proposal,
    infer_domain,
    merge_profile_override_for_plan_context,
    profile_context_changed_fields,
    profile_override_changes_plan_context,
    sync_proposal_fields,
)
from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.core.intents import INTENT_INFO, INTENT_MODIFY, INTENT_PLAN, normalize_intent
from app.core.persona_style import apply_persona_signature, normalize_plan_flow_preview
from app.core.profile_constraints import build_profile_constraint_set
from app.core.was_outbox import (
    enqueue_was_outbox,
    mark_was_outbox_succeeded,
    reconcile_pending_writes_with_outbox,
    replay_due_was_outbox,
)
from app.graph.nodes.generate import (
    _adjust_diet_plan_for_profile,
    _adjust_workout_plan_for_profile,
    _build_plan_delete_draft,
    _build_mixed_plan_clarification_draft,
    _build_modify_plan_fallback,
    _diet_plan_requires_safe_fallback,
    _expand_long_range_plan_if_requested,
    _generate_home_recommendations,
    _home_profile_issue_domains,
    _is_mixed_plan_type_request,
    _minimize_plan_exposition,
    _normalize_plan_core_message,
    _normalize_plan_approval_question,
    _plan_contract_needs_fallback,
    _persona_style_report,
    _render_plan_preview_from_items,
    _resolved_user_message,
    _resolve_proposed_plan_type,
    _response_render_state,
    _workout_item_category,
)
from app.graph.builder import route_generate_self_eval, route_intent
from app.graph.nodes.intent import (
    _looks_like_condition_info_question,
    _looks_like_ambiguous_mixed_plan_request,
    _looks_like_mixed_plan_clarification_followup,
    _looks_like_pending_sequential_plan_followup,
    _looks_like_simple_condition_statement,
    _routing_message,
    _support_mode,
)
from app.graph.nodes.context_resolver import (
    _QUESTION_MARKERS as _CONTEXT_QUESTION_MARKERS,
    _REFERENCE_MARKERS as _CONTEXT_REFERENCE_MARKERS,
    _resolve_context,
)
from app.graph.nodes.answer_validator import (
    _is_iso_date,
    _requires_external_fail_closed,
    _safe_diet_fallback_for_validation_failure,
    _safe_plan_fallback_for_semantic_failure,
    _semantic_validation_payload,
    _semantic_validation_mode,
    _should_run_semantic_validation,
    _validate_state,
    _validation_quality_dimensions,
)
from app.services.home_recommendations import (
    _home_profile_prompt_payload,
    _normalize_allergy_tokens,
    build_home_recommendation_prompt_input,
    kst_today_iso,
)
from app.graph.nodes.finalize import _looks_like_mojibake, _safe_response_from_state, make_finalize_node
from app.graph.nodes.preprocess import (
    _clear_active_proposal_updates,
    _mark_pending_write_failed,
    _normalize_user_profile,
    _normalize_today_plan,
    _normalize_pending_writes,
    _pending_write_exhausted,
    _pending_write_waiting_for_retry,
    _profile_context_changed,
    _should_clear_active_proposal_for_profile_change,
)
from app.graph.nodes.profile_constraints import (
    _profile_with_pending_changes,
    _resolved_query as _profile_constraints_resolved_query,
    make_profile_constraints_node,
)
from app.graph.nodes.record import _handle_plan_check, _handle_plan_delete, _handle_profile
from app.routers.chat import (
    _build_debug_state,
    _build_resumed_state,
    _build_state_summary,
    _hydrate_active_proposal,
    _hydrate_recent_dialogue,
    _merge_pending_writes,
    _merge_profile_changes,
    _next_pending_sequential_plan,
    _resolve_plan_write_fields,
    _was_write_status,
)
from app.schemas.chat import ChatRequest
from app.services.langsmith_quality import evaluate_trace_quality
from app.graph.nodes.retrieval_decision import (
    _build_decision as _build_retrieval_decision,
    _resolved_domain as _retrieval_decision_resolved_domain,
    _resolved_query as _retrieval_decision_resolved_query,
)
from app.graph.nodes.search import (
    _build_retrieval_spec,
    _rerank_external_results,
    _resolved_query as _search_resolved_query,
    _weak_external_should_fail_closed,
)
from app.schemas.was import to_plan_create_batches
from app.services.home_recommendations import normalize_home_recommendations, validate_home_recommendation_profile_fit
from app.schemas.home import (
    DietRecommendationItem,
    DietRecommendationSlots,
    HomeRecommendationResponse,
    WorkoutRecommendationItem,
    WorkoutRecommendationSlots,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_stretching_beats_cardio_label() -> None:
    item = {
        "name": "유산소 루틴",
        "detail": "가벼운 회복",
        "day": "2026-05-18",
        "ex_list": [{"exercise_name": "전신 스트레칭", "sets": 2, "calories": 40}],
    }
    assert_true(_workout_item_category(item) == "stretching", "stretching should win over cardio when exercise is stretching")

    payloads = to_plan_create_batches({"has_plan": True, "plan_type": "workout", "items": [item]})
    assert_true(payloads[0]["items"][0]["name"] == "스트레칭 루틴", "WAS payload should store stretching category")


def test_concrete_strength_exercises_override_stretching_label() -> None:
    today = date.fromisoformat(kst_today_iso())
    item = {
        "name": "스트레칭 루틴",
        "detail": "전신 가벼운 근력 운동",
        "day": today.isoformat(),
        "ex_list": [
            {"exercise_name": "덤벨 로우", "sets": 2, "calories": 40},
            {"exercise_name": "오버헤드 프레스", "sets": 2, "calories": 40},
        ],
    }

    payloads = to_plan_create_batches({"has_plan": True, "plan_type": "workout", "items": [item]})
    assert_true(
        payloads[0]["items"][0]["name"] == "상체 루틴",
        "concrete upper-body exercises should override a stale stretching category",
    )


def test_was_payload_aligns_past_week_plan_dates_to_today() -> None:
    today = date.fromisoformat(kst_today_iso())
    past_start = today - timedelta(days=3)
    items = [
        {
            "name": f"{offset + 1}일차 루틴",
            "detail": "가벼운 운동",
            "day": (past_start + timedelta(days=offset)).isoformat(),
            "ex_list": [{"exercise_name": "가벼운 걷기", "duration_minutes": 20, "calories": 80}],
        }
        for offset in range(7)
    ]

    payloads = to_plan_create_batches({"has_plan": True, "plan_type": "workout", "items": items})
    days = sorted(item["day"] for item in payloads[0]["items"])

    assert_true(days[0] == today.isoformat(), "past generated week plan should be shifted to start today")
    assert_true(days[-1] == (today + timedelta(days=6)).isoformat(), "shifted plan should preserve the week span")


def test_was_weekday_inference_never_schedules_past_day() -> None:
    today = date.fromisoformat(kst_today_iso())
    weekday_labels = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    yesterday_label = weekday_labels[(today.weekday() - 1) % 7]
    expected = today + timedelta(days=6)
    item = {
        "name": f"{yesterday_label} 상체 루틴",
        "detail": "푸쉬업",
        "ex_list": [{"exercise_name": "푸쉬업", "sets": 2, "calories": 40}],
    }

    payloads = to_plan_create_batches({"has_plan": True, "plan_type": "workout", "items": [item]})
    actual = date.fromisoformat(payloads[0]["items"][0]["day"])

    assert_true(actual == expected, "weekday inference should roll past weekdays to the next occurrence")


def test_profile_sensitive_workout_adjustment() -> None:
    plan = [
        {
            "name": "하체 루틴",
            "detail": "기본 하체 운동",
            "day": "2026-05-18",
            "ex_list": [
                {"exercise_name": "점프 스쿼트", "sets": 5, "calories": 120},
            ],
        }
    ]
    profile = {
        "age": 67,
        "weight": 92,
        "goal": "다이어트",
        "exercise_level": "beginner",
        "available_time_minutes": 15,
        "injury_history": ["무릎 통증"],
        "mbti": "INTJ",
    }
    adjusted = _adjust_workout_plan_for_profile(plan, profile)
    categories = [_workout_item_category(item) for item in adjusted]
    assert_true({"cardio", "stretching", "upper_body", "lower_body"}.issubset(set(categories)), "workout plan should cover four categories")
    names = " ".join(
        exercise["exercise_name"]
        for item in adjusted
        for exercise in item.get("ex_list", [])
    )
    assert_true("점프" not in names, "knee-sensitive beginner plan should avoid jumping")
    assert_true(any(token in names for token in ("의자", "브릿지", "자전거", "걷기")), "profile-sensitive safer substitutes should appear")


def test_diet_allergy_concrete_replacement() -> None:
    plan = [
        {
            "name": "Breakfast",
            "detail": "그릭요거트, 바나나, 견과류를 곁들인 아침",
            "day": "2026-05-18",
            "ex_list": [],
        }
    ]
    adjusted = _adjust_diet_plan_for_profile(plan, {"allergies": ["우유", "땅콩"], "goal": "근력 향상"})
    detail = adjusted[0]["detail"]
    assert_true("그릭요거트" not in detail and "견과류" not in detail, "allergen foods should be replaced")
    assert_true("콩요거트" in detail or "두유" in detail, "dairy replacement should be concrete")
    assert_true(not any(token in detail for token in ("알레르기", "고려", "제외", "대체")), "diet detail should not contain explanatory constraint text")


def test_diet_profile_concrete_adaptation() -> None:
    plan = [
        {
            "name": "Lunch",
            "detail": "Chicken breast and vegetables",
            "day": "2026-05-18",
            "ex_list": [],
        },
        {
            "name": "Dinner",
            "detail": "Greek yogurt and fruit",
            "day": "2026-05-18",
            "ex_list": [],
        },
    ]
    adjusted = _adjust_diet_plan_for_profile(
        plan,
        {
            "diet_type": "vegan",
            "diet_goal": "stable blood sugar",
            "medical_conditions": ["type 2 diabetes"],
            "goal": "muscle_gain",
        },
    )
    text = " ".join(item["detail"] for item in adjusted)
    assert_true("Chicken" not in text and "Greek yogurt" not in text, "plant-based diet should replace animal foods")
    assert_true(any(token in text for token in ("두부", "렌틸콩", "콩요거트")), "plant-based replacements should be concrete")
    assert_true("fruit" not in text and "블루베리" in text, "glucose-sensitive diet should choose a concrete lower-sugar fruit")


def test_plan_request_separation_and_question_copy() -> None:
    assert_true(infer_domain("운동이나 식단을 가볍게 잡아줘") == "general", "mixed workout/diet request should not pick one domain")
    assert_true(_is_mixed_plan_type_request("운동이나 식단을 가볍게 잡아줘"), "mixed plan request should be detected")
    assert_true(_is_mixed_plan_type_request("운동 계획과 식단 계획을 같이 짜줘"), "explicit both-domain request should be clarified before creating a plan")

    draft = _build_mixed_plan_clarification_draft()
    assert_true(not draft["proposed_plan"], "mixed ambiguous request should not create a merged plan")
    assert_true("따로 작성" in draft["draft_response"], "mixed clarification should mention separation")

    components = _normalize_plan_approval_question(
        normalize_draft_components({"core_message": "테스트", "approval_question": "시작하시겠어요?"}),
        "diet",
        "create",
    )
    assert_true(components["approval_question"] == "이 식단 플랜으로 작성할까요?", "plan confirmation copy should use 작성할까요")


def test_retrieval_goal_aliases_feed_external_filter() -> None:
    spec = _build_retrieval_spec(
        {
            "user_id": "goal-alias-test",
            "user_message": "diet plan for weight loss",
            "intent": "plan",
            "action_intent": "create",
            "domain": "diet",
            "user_profile": {"age": 17, "goal": "weight_loss"},
            "search_targets": [],
        },
        "diet plan for weight loss",
        [],
    )
    assert_true("fat_loss" in spec.goals, "weight_loss should normalize to the external KB fat_loss goal")
    assert_true("fat_loss" in str(spec.strict_filter), "strict Pinecone filter should include normalized fat_loss goal")


def test_retrieval_spec_sanitizes_malformed_profile_constraints() -> None:
    spec = _build_retrieval_spec(
        {
            "user_id": "malformed-retrieval-test",
            "user_message": "dairy-free high-protein diet plan",
            "intent": "plan",
            "action_intent": "create",
            "domain": "diet",
            "user_profile": "bad",
            "profile_constraints": {
                "should_use_rag": True,
                "profile_targets": "allergy",
                "retrieval_constraints": "dairy_allergy",
                "retrieval_critical_constraints": "dairy_allergy",
                "negative_constraints": "dairy_allowed",
                "goals": "muscle_gain",
            },
            "search_targets": [],
        },
        "dairy-free high-protein diet plan",
        [],
    )

    assert_true("vdb_external" in spec.targets, "malformed-safe profile constraints should still trigger external RAG")
    assert_true(spec.profile_targets == ["allergy"], "profile targets should not split scalar metadata into characters")
    assert_true(spec.constraints == ["dairy_allergy"], "retrieval constraints should not split scalar metadata into characters")
    assert_true(
        spec.critical_constraints == ["dairy_allergy"],
        "critical retrieval constraints should not split scalar metadata into characters",
    )
    assert_true(spec.negative_constraints == ["dairy_allowed"], "negative constraints should not split scalar metadata")
    assert_true("muscle_gain" in spec.goals, "goal metadata should normalize scalar values")
    assert_true("dairy_allergy" in str(spec.strict_filter), "strict Pinecone filter should retain sanitized constraint")
    assert_true("'d'" not in str(spec.strict_filter), "strict Pinecone filter should not contain per-character constraints")

    fallback_spec = _build_retrieval_spec(
        {
            "user_id": "bad-profile-test",
            "user_message": "diet plan with evidence",
            "intent": "plan",
            "action_intent": "create",
            "domain": "diet",
            "user_profile": "bad",
            "profile_constraints": "bad",
            "search_targets": [],
        },
        "diet plan with evidence",
        [],
    )
    assert_true(fallback_spec.domain == "diet", "bad profile objects should not crash retrieval spec generation")

    scalar_target_spec = _build_retrieval_spec(
        {
            "user_id": "scalar-target-test",
            "user_message": "quick workout plan",
            "intent": "plan",
            "action_intent": "create",
            "domain": "workout",
            "user_profile": {},
            "profile_constraints": {"should_use_rag": False},
            "search_targets": "vdb_external",
        },
        "quick workout plan",
        "vdb_external",
    )
    assert_true(
        scalar_target_spec.targets == [],
        "scalar search target should not split into characters and should still respect RAG trigger gating",
    )

    info_target_spec = _build_retrieval_spec(
        {
            "user_id": "info-target-test",
            "user_message": "latest protein guideline",
            "intent": INTENT_INFO,
            "action_intent": "info",
            "domain": "diet",
            "user_profile": {},
            "profile_constraints": {},
            "search_targets": ["vdb_external", "bad", "vdb_external"],
        },
        "protein guideline",
        ["vdb_external", "bad", "vdb_external"],
    )
    assert_true(info_target_spec.targets == ["vdb_external"], "search targets should be known and deduped")


def test_malformed_confidence_intensity_and_scores_are_safe() -> None:
    state = {
        "user_id": "malformed-numeric-test",
        "user_message": "original diet plan request",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "diet",
        "context_resolution": {
            "resolved_reference": "recent_chat",
            "resolved_domain": "diet",
            "resolved_text": "stale rewritten context",
            "confidence": "not-a-number",
            "ambiguous": False,
        },
        "emotion": {"label": "neutral", "intensity": "not-a-number"},
        "user_profile": {},
        "profile_constraints": {"should_use_rag": False},
        "search_targets": [],
    }

    assert_true(_search_resolved_query(state) == "original diet plan request", "search query should ignore malformed confidence")
    assert_true(
        _profile_constraints_resolved_query(state) == "original diet plan request",
        "profile constraints query should ignore malformed confidence",
    )
    assert_true(
        _retrieval_decision_resolved_query(state) == "original diet plan request",
        "retrieval decision query should ignore malformed confidence",
    )
    assert_true(_resolved_user_message(state) == "original diet plan request", "generator should ignore malformed confidence")
    assert_true(
        _routing_message(state, "original diet plan request") == "original diet plan request",
        "intent routing should ignore malformed confidence",
    )
    assert_true(_support_mode(INTENT_PLAN, state, "plain plan request") == "normal", "malformed emotion intensity should not force care mode")

    spec = _build_retrieval_spec(
        {
            **state,
            "profile_constraints": {
                "should_use_rag": True,
                "retrieval_constraints": ["dairy_allergy"],
                "retrieval_critical_constraints": ["dairy_allergy"],
            },
        },
        "dairy-free diet plan",
        ["vdb_external"],
    )
    reranked = _rerank_external_results(
        [
            {
                "source": "external",
                "source_type": "external_kb",
                "domain": "diet",
                "constraints": [],
                "score": "not-a-number",
            },
            {
                "source": "external",
                "source_type": "external_kb",
                "domain": "diet",
                "constraints": ["dairy_allergy"],
                "score": "also-bad",
            },
        ],
        spec,
    )
    assert_true(
        reranked[0]["constraints"] == ["dairy_allergy"],
        "malformed vector scores should not prevent metadata-based reranking",
    )


def test_retrieval_decision_sanitizes_targets_and_context() -> None:
    state = {
        "user_id": "retrieval-decision-safe",
        "user_message": "diet plan",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "diet",
        "context_resolution": "bad",
        "profile_constraints": "bad",
        "search_targets": "vdb_external",
    }
    assert_true(
        _retrieval_decision_resolved_query(state) == "diet plan",
        "retrieval decision query should tolerate malformed context resolution",
    )
    assert_true(
        _retrieval_decision_resolved_domain({**state, "domain": "bad"}, "diet plan") == "diet",
        "retrieval decision domain should fall back to inferred domain with malformed context",
    )
    low_risk_decision = _build_retrieval_decision(state, "diet plan", "vdb_external")
    assert_true(
        low_risk_decision["targets"] == [],
        "scalar retrieval targets should be treated as one target and still respect low-risk plan gating",
    )

    info_decision = _build_retrieval_decision(
        {
            **state,
            "intent": INTENT_INFO,
            "action_intent": "info",
            "profile_constraints": {},
            "search_targets": ["vdb_external", "bad", "web", "vdb_external"],
        },
        "protein guideline",
        ["vdb_external", "bad", "web", "vdb_external"],
    )
    assert_true(info_decision["targets"] == ["vdb_external"], "retrieval decision should dedupe and drop invalid/web-low-recency targets")


def test_home_recommendation_display_bounds() -> None:
    raw = HomeRecommendationResponse(
        date="2026-05-18",
        scope="workout",
        workout=WorkoutRecommendationSlots(
            stretching=WorkoutRecommendationItem(
                exercise_name="전신 스트레칭",
                summary="아주 길고 장황한 설명이 카드에서 너무 길게 보이지 않도록 줄여야 하는 회복 루틴입니다.",
                duration_minutes=12,
                calories=40,
            )
        ),
    )
    normalized = normalize_home_recommendations(raw, scope="workout", date="2026-05-18")
    stretching = normalized.workout.stretching
    assert_true(stretching is not None, "stretching fallback should exist")
    assert_true(stretching.duration_minutes is None, "stretching should not carry cardio duration")
    assert_true(stretching.sets is not None, "stretching should use sets")
    assert_true(len(stretching.summary) <= 46, "summary should be display-bounded")

    long_name = "x" * 200
    raw_bounds = HomeRecommendationResponse(
        date="2026-05-18",
        scope="all",
        workout=WorkoutRecommendationSlots(
            upper_body=WorkoutRecommendationItem(
                exercise_name=long_name,
                summary=long_name,
                sets=999,
                calories=999999,
            ),
            cardio=WorkoutRecommendationItem(
                exercise_name=long_name,
                summary=long_name,
                duration_minutes=-30,
                calories=999999,
            ),
        ),
        diet=DietRecommendationSlots(
            breakfast=DietRecommendationItem(food_name=long_name, summary=long_name, calories=999999)
        ),
    )
    bounded = normalize_home_recommendations(raw_bounds, scope="all", date="2026-05-18", user_profile="bad")
    assert_true(len(bounded.workout.upper_body.exercise_name) <= 34, "home workout name should be display-bounded")
    assert_true(bounded.workout.upper_body.sets == 20, "home workout sets should be capped")
    assert_true(bounded.workout.upper_body.calories == 1000, "home workout calories should be capped")
    assert_true(bounded.workout.cardio.duration_minutes == 1, "home cardio duration should be bounded")
    assert_true(bounded.diet.breakfast.calories == 2000, "home diet calories should be capped")
    assert_true(len(bounded.diet.breakfast.food_name) <= 34, "home diet food name should be display-bounded")


def test_home_recommendation_replaces_profile_conflicts() -> None:
    raw = HomeRecommendationResponse(
        date="2026-05-18",
        scope="diet",
        diet=DietRecommendationSlots(
            breakfast=DietRecommendationItem(
                food_name="그릭요거트와 견과류",
                summary="유제품과 견과가 들어간 아침",
                calories=360,
            ),
            lunch=DietRecommendationItem(
                food_name="두부 스테이크",
                summary="대두 중심 점심",
                calories=480,
            ),
            dinner=DietRecommendationItem(
                food_name="닭가슴살 샐러드",
                summary="고기 중심 저녁",
                calories=420,
            ),
        ),
    )
    normalized = normalize_home_recommendations(
        raw,
        scope="diet",
        date="2026-05-18",
        user_profile={
            "diet_type": "vegetarian",
            "allergies": ["dairy", "nut", "soy"],
        },
    )
    text = " ".join(
        item.food_name
        for item in (
            normalized.diet.breakfast,
            normalized.diet.lunch,
            normalized.diet.dinner,
        )
        if item is not None
    )

    assert_true(
        not any(token in text for token in ("그릭요거트", "견과", "두부", "두유", "닭가슴살")),
        "home diet recommendations should replace LLM items that conflict with profile constraints",
    )


def test_home_recommendation_guard_flags_medical_synonyms() -> None:
    raw = HomeRecommendationResponse(
        date="2026-05-18",
        scope="diet",
        diet=DietRecommendationSlots(
            breakfast=DietRecommendationItem(
                food_name="Whey protein shake",
                summary="high protein casein smoothie",
                calories=380,
            ),
            lunch=DietRecommendationItem(
                food_name="Anchovy shellfish ramen",
                summary="salty broth with mackerel",
                calories=620,
            ),
            dinner=DietRecommendationItem(
                food_name="Unpasteurized cheese plate",
                summary="OMAD detox dinner",
                calories=420,
            ),
        ),
    )
    issues = validate_home_recommendation_profile_fit(
        raw,
        user_profile={
            "medical_conditions": ["renal disease", "gout", "pregnancy", "eating disorder risk"],
            "dietary_restrictions": ["very low calorie request should be rejected"],
        },
    )
    codes = {issue["code"] for issue in issues}
    assert_true("home_diet_profile_conflict" in codes, "home guard should flag medical diet synonym conflicts")

    normalized = normalize_home_recommendations(
        raw,
        scope="diet",
        date="2026-05-18",
        user_profile={
            "medical_conditions": ["renal disease", "gout", "pregnancy", "eating disorder risk"],
            "dietary_restrictions": ["very low calorie request should be rejected"],
        },
    )
    repaired_issues = validate_home_recommendation_profile_fit(
        normalized,
        user_profile={
            "medical_conditions": ["renal disease", "gout", "pregnancy", "eating disorder risk"],
            "dietary_restrictions": ["very low calorie request should be rejected"],
        },
    )
    assert_true(not repaired_issues, "home normalization should replace medically unsafe slots")


def test_home_recommendation_routes_through_validator() -> None:
    assert_true(
        route_generate_self_eval(
            {
                "request_kind": "home_recommendation",
                "home_recommendations": empty_home_recommendation_payload(),
            }
        )
        == "answer_validator",
        "home recommendation responses should pass through the answer validator",
    )


def test_home_recommendation_validator_blocks_profile_conflicts() -> None:
    raw = HomeRecommendationResponse(
        date="2026-06-01",
        scope="workout",
        workout=WorkoutRecommendationSlots(
            cardio=WorkoutRecommendationItem(
                exercise_name="HIIT sprint jumps",
                summary="high intensity jump interval",
                duration_minutes=30,
                calories=180,
            )
        ),
    )
    profile = {
        "age": 67,
        "fitness_level": "beginner",
        "activityLevel": "low",
        "available_time_minutes": 8,
        "exercise_frequency": 1,
    }
    issues = validate_home_recommendation_profile_fit(raw, user_profile=profile)
    codes = {issue["code"] for issue in issues}
    assert_true("home_workout_time_conflict" in codes, "home guard should catch available-time conflicts")

    report = _validate_state(
        {
            "request_kind": "home_recommendation",
            "home_recommendations": raw.model_dump(),
            "user_profile": profile,
        }
    )
    assert_true(not report["passed"], "home recommendation validator should block critical profile conflicts")

    normalized = normalize_home_recommendations(
        raw,
        scope="workout",
        date="2026-06-01",
        user_profile=profile,
    )
    repaired_issues = validate_home_recommendation_profile_fit(normalized, user_profile=profile)
    assert_true(
        not any(issue["severity"] == "critical" for issue in repaired_issues),
        "home normalization should replace workout slots that still conflict with profile constraints",
    )
    assert_true(
        normalized.workout.cardio is not None and (normalized.workout.cardio.duration_minutes or 0) <= 8,
        "home cardio fallback should fit available_time_minutes",
    )


def test_home_recommendation_blocks_advanced_risk_taxonomy() -> None:
    raw = HomeRecommendationResponse(
        date="2026-06-01",
        scope="workout",
        workout=WorkoutRecommendationSlots(
            lower_body=WorkoutRecommendationItem(
                exercise_name="민첩성 파워 서킷",
                summary="빠르게 반복하는 plyometric power circuit",
                sets=4,
                calories=160,
            )
        ),
    )
    issues = validate_home_recommendation_profile_fit(
        raw,
        user_profile={"exercise_level": "beginner", "activityLevel": "low"},
    )
    codes = {issue["code"] for issue in issues}
    assert_true("home_workout_level_conflict" in codes, "expanded home risk taxonomy should catch advanced beginner conflicts")


def test_home_generation_quality_flags_capture_raw_repairs() -> None:
    class FakeHomeRouter:
        async def generate(self, *, system_prompt: str, user_content: str, response_schema):  # noqa: ANN001
            raw = HomeRecommendationResponse(
                date="2026-06-01",
                scope="workout",
                workout=WorkoutRecommendationSlots(
                    cardio=WorkoutRecommendationItem(
                        exercise_name="HIIT sprint jumps",
                        summary="high intensity jump interval",
                        duration_minutes=30,
                        calories=180,
                    )
                ),
            )
            return raw.model_dump_json()

    class FakeTrace:
        def record_current_event(self, **kwargs):  # noqa: ANN003
            return None

        def record_current_alert(self, **kwargs):  # noqa: ANN003
            return None

    class FakeDeps:
        router = FakeHomeRouter()
        trace = FakeTrace()

    result = asyncio.run(
        _generate_home_recommendations(
            FakeDeps(),
            {
                "home_recommendation_scope": "workout",
                "user_profile": {
                    "age": 67,
                    "fitness_level": "beginner",
                    "available_time_minutes": 8,
                },
                "today_plan": [],
                "home_recommendation_recent": {},
            },
            0.0,
        )
    )
    flags = result["generation_quality_flags"]
    assert_true(flags["home_profile_fit_raw_issue_count"] > 0, "home flags should retain raw unsafe recommendation issues")
    assert_true(flags["home_profile_fit_issue_count"] == 0, "home normalization should repair raw unsafe recommendations")
    assert_true(flags["home_profile_fit_repaired"] is True, "home flags should mark profile-fit repair after normalization")
    assert_true("workout" in flags["home_profile_fit_original_domains"], "home flags should identify the repaired workout section")
    assert_true("workout" in flags["home_profile_fit_raw_domains"], "home flags should identify raw unsafe workout sections")
    assert_true(flags["home_profile_fit_remaining_domains"] == [], "home flags should expose remaining unsafe sections after repair")


def empty_home_recommendation_payload() -> dict:
    return HomeRecommendationResponse(date="2026-06-01", scope="all").model_dump()


def test_iso_date_rejects_invalid_calendar_dates() -> None:
    assert_true(not _is_iso_date("2026-02-31"), "invalid calendar date should fail ISO validation")
    assert_true(_is_iso_date("2024-02-29"), "valid leap-day date should pass ISO validation")


def test_profile_refresh_change_invalidates_active_proposal_context() -> None:
    assert_true(
        _profile_context_changed({"allergies": []}, {"allergies": ["milk"]}),
        "profile safety fields should invalidate active plan context",
    )
    assert_true(
        not _profile_context_changed(
            {"selected_ai_persona": "cheer_sis", "allergies": []},
            {"selected_ai_persona": "daily_manager", "allergies": []},
        ),
        "persona-only changes should not invalidate active plan data",
    )
    assert_true(
        not _profile_context_changed({"activity_level": "low"}, {"activityLevel": "low"}),
        "activity_level aliases should not look like a profile change",
    )
    assert_true(
        not _profile_context_changed({"allergies": ["milk"]}, {"allergy": "milk"}),
        "allergy aliases should compare against canonical allergy context",
    )
    assert_true(
        profile_context_changed_fields({"fitness_level": "beginner"}, {"exercise_level": "advanced"}) == ["exercise_level"],
        "fitness aliases should report the canonical field that changed",
    )
    assert_true(
        canonical_profile_context({"medical_conditions": {"primary": "kidney"}})
        != canonical_profile_context({"medical_conditions": {"secondary": "kidney"}}),
        "canonical profile dict values should keep keys so different structured fields do not collapse",
    )


def test_home_profile_aliases_feed_prompt_and_allergy_guard() -> None:
    profile = {
        "activityLevel": "low",
        "fitness_level": "beginner",
        "allergy": "milk",
        "otherAllergy": ["soy"],
    }
    payload = _home_profile_prompt_payload(profile)
    assert_true(payload["activity_level"] == "low", "home prompt should normalize activityLevel")
    assert_true(payload["exercise_level"] == "beginner", "home prompt should normalize fitness_level")
    assert_true(set(payload["allergies"]) == {"milk", "soy"}, "home prompt should merge allergy aliases")

    allergy_tokens = _normalize_allergy_tokens(profile)
    assert_true({"dairy", "soy"}.issubset(allergy_tokens), "home allergy guard should read allergy aliases")


def test_profile_override_clears_canonical_alias_siblings() -> None:
    saved_profile = {"allergy": "milk", "activityLevel": "low"}
    override = {"allergies": [], "activity_level": "low"}
    merged = merge_profile_override_for_plan_context(saved_profile, override)
    canonical = canonical_profile_context(merged)
    assert_true(canonical.get("allergies", "") in {"", ()}, "empty allergies override should clear sibling allergy aliases")
    assert_true(
        profile_override_changes_plan_context(saved_profile, override),
        "clearing a saved allergy through a canonical alias should invalidate active plan context",
    )


def test_profile_change_clears_pending_sequential_context() -> None:
    state = {
        "user_profile": {"allergies": []},
        "pending_sequential_plan": {
            "domain": "diet",
            "reason": "mixed_workout_diet_sequence",
            "created_turn": 2,
        },
    }
    next_profile = {"allergies": ["milk"]}
    assert_true(
        _should_clear_active_proposal_for_profile_change(state, next_profile),
        "profile changes should invalidate queued sequential plans even without an active proposal",
    )
    updates = _clear_active_proposal_updates()
    assert_true(updates["pending_sequential_plan"] is None, "profile invalidation should clear queued sequential plans")


def test_was_profile_merge_clears_alias_siblings_and_plan_context() -> None:
    merged = _merge_profile_changes(
        {
            "allergy": "milk",
            "activityLevel": "low",
            "selected_ai_persona": "cheer_sis",
        },
        {
            "allergies": [],
            "activity_level": "low",
            "_idempotency_key": "ignore-me",
        },
    )
    assert_true("allergy" not in merged, "WAS profile merge should clear sibling allergy aliases")
    assert_true("_idempotency_key" not in merged, "WAS profile merge should not retain write metadata")
    assert_true(merged["selected_ai_persona"] == "cheer_sis", "WAS profile merge should keep unrelated profile fields")


def test_profile_constraints_merge_clears_aliases_and_metadata() -> None:
    merged = _profile_with_pending_changes(
        {"allergies": ["none"], "activity_level": "medium", "weight": 70},
        {
            "allergy": "milk",
            "activityLevel": "low",
            "_idempotency_key": "ignore",
            "write_type": "profile",
        },
    )
    assert_true("allergies" not in merged, "profile constraints merge should clear stale allergy aliases")
    assert_true("activity_level" not in merged, "profile constraints merge should clear stale activity aliases")
    assert_true("_idempotency_key" not in merged and "write_type" not in merged, "profile constraints merge should drop write metadata")
    assert_true(merged["allergy"] == "milk" and merged["activityLevel"] == "low", "profile constraints merge should retain new profile values")


def test_profile_constraints_node_clears_pending_sequential_plan() -> None:
    class FakeTrace:
        def record_current_event(self, **kwargs):  # noqa: ANN003
            self.last_event = kwargs

    class FakeDeps:
        trace = FakeTrace()

    node = make_profile_constraints_node(FakeDeps())
    result = asyncio.run(
        node(
            {
                "user_message": "식단도",
                "domain": "diet",
                "user_profile": {"allergies": []},
                "profile_changes": {"allergy": "milk"},
                "pending_sequential_plan": {
                    "domain": "diet",
                    "reason": "mixed_workout_diet_sequence",
                    "created_turn": 2,
                },
                "active_proposal": {"domain": "workout", "write_mode": "create", "items": [], "summary": "", "last_used_turn": 2},
                "awaiting_plan_confirmation": True,
                "proposed_plan": [{"name": "기존 운동"}],
                "proposed_plan_type": "workout",
                "proposed_plan_action": "create",
            }
        )
    )
    assert_true(result["active_proposal"] is None, "profile constraints should clear stale active proposal")
    assert_true(result["pending_sequential_plan"] is None, "profile constraints should clear queued sequential plan")
    assert_true(result["awaiting_plan_confirmation"] is False, "profile constraints should clear stale approval state")


def test_profile_constraints_tolerates_malformed_profile_state() -> None:
    constraints = build_profile_constraint_set(["bad-profile"], "diet plan", domain="Diet")
    assert_true(constraints["domain"] == "diet", "profile constraints should normalize malformed domain input")
    assert_true(
        constraints["profile_field_coverage"]["present_count"] == 0,
        "malformed profile containers should be treated as empty profile coverage",
    )

    class FakeTrace:
        def record_current_event(self, **kwargs):  # noqa: ANN003
            self.last_event = kwargs

    class FakeDeps:
        trace = FakeTrace()

    node = make_profile_constraints_node(FakeDeps())
    result = asyncio.run(
        node(
            {
                "user_message": "make a diet plan",
                "domain": "diet",
                "context_resolution": "bad",
                "effective_user_profile": ["bad"],
                "user_profile": "bad",
                "pending_profile_overlay": "bad",
                "profile_changes": {"allergy": "dairy allergy", "available_time_minutes": 10},
            }
        )
    )
    profile_constraints = result["profile_constraints"]
    assert_true("low_time" in profile_constraints["profile_constraints"], "pending low-time change should still feed constraints")
    assert_true(
        profile_constraints["profile_field_coverage"]["present_count"] >= 2,
        "pending profile changes should survive malformed saved profile state",
    )
    assert_true(result["effective_user_profile"]["allergy"] == "dairy allergy", "effective profile should retain pending allergy change")
    assert_true(isinstance(result["pending_profile_overlay"], dict), "pending overlay should be normalized to a dict")


def test_explicit_both_plan_request_clarifies() -> None:
    message = "운동 계획과 식단 계획을 같이 짜줘"
    assert_true(_is_mixed_plan_type_request(message), "generator should clarify explicit both-domain plan requests")
    assert_true(
        _looks_like_ambiguous_mixed_plan_request(message),
        "intent router should clarify explicit both-domain plan requests",
    )


def test_simple_condition_statement_routes_casual() -> None:
    assert_true(
        _looks_like_simple_condition_statement("오늘 피곤해"),
        "plain condition statements should remain casual",
    )
    assert_true(
        _looks_like_simple_condition_statement("잠을 못 잤어"),
        "poor sleep statements should remain casual",
    )
    assert_true(
        _looks_like_simple_condition_statement("식욕이 확 올라"),
        "appetite spike statements should remain casual",
    )
    assert_true(
        _looks_like_simple_condition_statement("허리가 뻐근해"),
        "back stiffness statements should remain casual",
    )
    assert_true(
        not _looks_like_simple_condition_statement("오늘 피곤한데 운동 쉬어도 돼?"),
        "condition questions should still route to useful guidance",
    )
    assert_true(
        _looks_like_condition_info_question("오늘 피곤한데 운동 쉬어도 돼?"),
        "recovery/rest condition questions should route to info",
    )
    assert_true(
        not _looks_like_simple_condition_statement("잠을 못 자서 불안해"),
        "mixed condition plus emotion should not be treated as plain casual condition",
    )
    assert_true(
        not _looks_like_simple_condition_statement("허리 아픈데 운동 추천해줘"),
        "condition plus workout recommendation should not be treated as plain casual condition",
    )


def test_mixed_plan_clarification_followup_starts_workout_first() -> None:
    state = {
        "recent_dialogue": {
            "recent_turns": [
                {
                    "assistant_text": "운동 플랜과 식단 플랜은 따로 작성할게요. 먼저 하나를 골라주세요.",
                }
            ]
        }
    }
    assert_true(
        _looks_like_mixed_plan_clarification_followup("둘 다 해줘", state),
        "both-domain clarification follow-up should be detected for sequential handling",
    )
    assert_true(
        _looks_like_mixed_plan_clarification_followup("운동이랑 식단 둘 다 해줘", state),
        "explicit both-domain clarification follow-up should still start sequential handling instead of clarifying again",
    )


def test_pending_sequential_followup_routes_next_domain() -> None:
    state = {
        "pending_sequential_plan": {
            "domain": "diet",
            "reason": "mixed_workout_diet_sequence",
            "created_turn": 2,
        },
        "awaiting_plan_confirmation": False,
        "active_proposal": None,
        "proposed_plan": None,
    }
    assert_true(
        _looks_like_pending_sequential_plan_followup("이어서 식단도 해줘", state),
        "pending sequential follow-up should route to the queued diet domain",
    )
    assert_true(
        _looks_like_pending_sequential_plan_followup("식단도", state),
        "short queued-domain follow-up should route to the pending diet domain",
    )
    assert_true(
        _looks_like_pending_sequential_plan_followup("그럼 식단까지", state),
        "domain plus continuation suffix should route to the pending diet domain",
    )
    assert_true(
        _looks_like_pending_sequential_plan_followup("diet too", state),
        "English short queued-domain follow-up should route to the pending diet domain",
    )
    assert_true(
        not _looks_like_pending_sequential_plan_followup("운동도", state),
        "opposite-domain short follow-up should not consume a pending diet queue",
    )
    assert_true(
        not _looks_like_pending_sequential_plan_followup(
            "이어서 식단도 해줘",
            {**state, "awaiting_plan_confirmation": True, "active_proposal": {"domain": "workout", "items": []}},
        ),
        "pending sequential follow-up should wait while the current proposal is still awaiting confirmation",
    )


def test_pending_sequential_checkpoint_policy() -> None:
    previous_state = {
        "pending_sequential_plan": {
            "domain": "diet",
            "reason": "mixed_workout_diet_sequence",
            "created_turn": 3,
        },
        "turn_count": 3,
    }
    approval_result = {"action_intent": "approval", "turn_count": 4}
    assert_true(
        (_next_pending_sequential_plan(previous_state, approval_result, None) or {}).get("domain") == "diet",
        "pending sequential plan should remain after approval writes the first domain",
    )
    unrelated_workout = {"action_intent": "create", "domain": "workout", "turn_count": 5}
    assert_true(
        _next_pending_sequential_plan(
            previous_state,
            unrelated_workout,
            {"domain": "workout", "items": [], "write_mode": "create", "summary": "", "last_used_turn": 5},
        )
        is None,
        "starting a different domain proposal should clear stale queued diet",
    )
    assert_true(
        _next_pending_sequential_plan(previous_state, {"action_intent": "casual", "turn_count": 9}, None) is None,
        "old pending sequential plans should expire",
    )
    assert_true(
        _next_pending_sequential_plan(previous_state, {"action_intent": "safety", "turn_count": 4}, None) is None,
        "safety turns should clear queued sequential plans",
    )
    malformed_turn_pending = _next_pending_sequential_plan(
        {
            "pending_sequential_plan": {
                "domain": "diet",
                "reason": "mixed",
                "created_turn": {"bad": 1},
            },
            "turn_count": "bad",
        },
        {"action_intent": "casual", "turn_count": ["bad"]},
        None,
    )
    assert_true(
        malformed_turn_pending is not None and malformed_turn_pending["created_turn"] == 0,
        "malformed pending sequential turns should be normalized instead of crashing",
    )
    future_pending = _next_pending_sequential_plan(
        {
            "pending_sequential_plan": {
                "domain": "diet",
                "reason": "x" * 120,
                "created_turn": 99,
            },
            "turn_count": 4,
        },
        {"action_intent": "casual", "turn_count": 4},
        None,
    )
    assert_true(future_pending is not None and future_pending["created_turn"] == 4, "future pending turns should be clamped")
    assert_true(len(future_pending["reason"]) <= 80, "pending sequential reasons should be bounded")


def test_resumed_checkpoint_sanitizes_proposal_and_pending_state() -> None:
    assert_true(
        _hydrate_active_proposal(
            {
                "active_proposal": {
                    "domain": "workout",
                    "write_mode": "create",
                    "items": [{"name": "오래된 제안"}],
                    "summary": "old",
                    "last_used_turn": 1,
                }
            },
            current_turn=3,
        )
        is None,
        "stale checkpoint active proposal should not remain approvable",
    )
    assert_true(
        _hydrate_active_proposal(
            {
                "active_proposal": {
                    "domain": "workout",
                    "write_mode": "delete",
                    "items": [{"name": "미래 제안"}],
                    "summary": "x" * 200,
                    "last_used_turn": 99,
                }
            },
            current_turn=4,
        )["last_used_turn"]
        == 4,
        "future checkpoint active proposal turns should be clamped",
    )
    assert_true(
        _hydrate_active_proposal(
            {
                "active_proposal": {
                    "domain": "general",
                    "write_mode": "create",
                    "items": [{"name": "잘못된 도메인"}],
                    "last_used_turn": 4,
                }
            },
            current_turn=4,
        )
        is None,
        "invalid checkpoint active proposal domains should be dropped",
    )
    resumed = _build_resumed_state(
        ChatRequest(user_id="resume-test", user_message="식단도"),
        {
            "turn_count": 5,
            "pending_sequential_plan": {
                "domain": "diet",
                "reason": "x" * 160,
                "created_turn": 99,
            },
            "active_proposal": {
                "domain": "workout",
                "write_mode": "create",
                "items": [{"name": "현재 제안"}],
                "last_used_turn": 5,
            },
        },
    )
    assert_true(resumed["pending_sequential_plan"]["created_turn"] == 5, "resumed pending sequential turn should be bounded")
    assert_true(len(resumed["pending_sequential_plan"]["reason"]) <= 80, "resumed pending sequential reason should be bounded")
    assert_true(resumed["awaiting_plan_confirmation"] is True, "valid resumed active proposal should keep approval state")


def test_recent_dialogue_hydration_sanitizes_checkpoint_memory() -> None:
    long_text = "x" * 500
    hydrated = _hydrate_recent_dialogue(
        {
            "recent_dialogue": {
                "recent_turns": [
                    "drop",
                    {
                        "turn_id": -5,
                        "user_text": long_text,
                        "assistant_text": long_text,
                        "action_intent": "invalid",
                        "domain": "invalid",
                        "support_mode": "invalid",
                        "referenced_object": "invalid",
                        "state_effect": "invalid",
                    },
                ]
                + [
                    {
                        "turn_id": index,
                        "user_text": f"user-{index}",
                        "assistant_text": f"assistant-{index}",
                        "action_intent": "create",
                        "domain": "diet",
                        "support_mode": "normal",
                        "referenced_object": "active_proposal",
                        "state_effect": "proposal_created",
                    }
                    for index in range(6)
                ],
            }
        }
    )
    turns = hydrated["recent_turns"]
    assert_true(len(turns) == 4, "recent dialogue should keep only bounded recent turns")
    assert_true(turns[0]["turn_id"] == 2 and turns[-1]["turn_id"] == 5, "recent dialogue should preserve newest valid turns")
    assert_true(turns[-1]["domain"] == "diet", "valid recent dialogue enum values should be preserved")

    sanitized_bad = _hydrate_recent_dialogue(
        {
            "recent_dialogue": {
                "recent_turns": [
                    {
                        "turn_id": -5,
                        "user_text": long_text,
                        "assistant_text": long_text,
                        "action_intent": "invalid",
                        "domain": "invalid",
                        "support_mode": "invalid",
                        "referenced_object": "invalid",
                        "state_effect": "invalid",
                    }
                ],
            }
        }
    )["recent_turns"][0]
    assert_true(sanitized_bad["turn_id"] == 0, "negative recent turn ids should be clamped")
    assert_true(len(sanitized_bad["user_text"]) == 320, "recent dialogue user text should be bounded")
    assert_true(len(sanitized_bad["assistant_summary"]) == 100, "recent dialogue summaries should be bounded")
    assert_true(sanitized_bad["action_intent"] == "fallback", "invalid action intent should be normalized")
    assert_true(sanitized_bad["domain"] == "general", "invalid domain should be normalized")
    assert_true(sanitized_bad["support_mode"] == "normal", "invalid support mode should be normalized")
    assert_true(sanitized_bad["referenced_object"] == "none", "invalid references should be normalized")
    assert_true(sanitized_bad["state_effect"] == "none", "invalid state effects should be normalized")

    legacy = _hydrate_recent_dialogue(
        {
            "recent_dialogue": "bad",
            "turn_count": 3,
            "messages": [
                "drop",
                {"role": "user", "content": long_text},
                {"role": "assistant", "content": long_text},
            ],
        }
    )
    assert_true(len(legacy["recent_turns"]) == 1, "legacy messages should hydrate one paired turn")
    assert_true(len(legacy["recent_turns"][0]["user_text"]) == 320, "legacy message hydration should bound text")


def test_home_profile_issue_domains_are_section_specific() -> None:
    domains = _home_profile_issue_domains(
        [
            {"slot": "cardio", "code": "home_workout_level_conflict"},
            {"slot": "breakfast", "code": "home_diet_profile_conflict"},
            {"code": "home_workout_profile_conflict"},
            {"code": "home_diet_profile_conflict"},
        ]
    )
    assert_true(domains == ["diet", "workout"], "home quality flags should preserve affected recommendation sections")


def test_mixed_active_proposal_is_sanitized() -> None:
    active = {
        "domain": "workout",
        "write_mode": "create",
        "items": [
            {"name": "Workout", "day": kst_today_iso(), "ex_list": [{"exercise_name": "Walk"}]},
            {"name": "Breakfast", "type": "meal", "day": kst_today_iso(), "detail": "Oats", "ex_list": []},
        ],
        "summary": "mixed",
        "last_used_turn": 1,
    }
    assert_true(active_proposal_is_mixed_domain(active), "mixed active proposal should be detected")
    assert_true(sync_proposal_fields(active)["active_proposal"] is None, "mixed active proposal should not be persisted")

    legacy_active = {
        "domain": "workout",
        "write_mode": "create",
        "items": [
            {"name": "Workout", "day": kst_today_iso(), "ex_list": [{"exercise_name": "Walk"}]},
            {"name": "Breakfast", "day": kst_today_iso(), "detail": "Oats and yogurt", "ex_list": []},
        ],
        "summary": "legacy mixed",
        "last_used_turn": 1,
    }
    assert_true(active_proposal_is_mixed_domain(legacy_active), "legacy untyped meal items should be detected as mixed")


def test_conversation_state_helpers_sanitize_malformed_inputs() -> None:
    assert_true(
        build_active_proposal(
            {
                "proposed_plan": "bad",
                "proposed_plan_type": "workout",
                "turn_count": {"bad": 1},
            }
        )
        is None,
        "core active proposal builder should not split malformed proposed_plan strings",
    )

    proposal = build_active_proposal(
        {
            "proposed_plan": [
                {"name": "Walk", "day": kst_today_iso(), "ex_list": [{"exercise_name": "Walk"}]},
                "drop",
            ],
            "proposed_plan_type": "workout",
            "turn_count": "bad",
            "draft_components": "bad",
        }
    )
    assert_true(proposal is not None and proposal["last_used_turn"] == 0, "core active proposal turn should be safe")
    assert_true(len(proposal["items"]) == 1, "core active proposal should keep only dict plan items")

    evolved = evolve_active_proposal(
        {"domain": "workout", "write_mode": "create", "items": [{"name": "old"}], "summary": "old", "last_used_turn": "bad"},
        {"turn_count": ["bad"], "action_intent": "casual", "context_resolution": "bad"},
    )
    assert_true(evolved is not None and evolved["last_used_turn"] == 0, "malformed turn state should be normalized during proposal evolution")

    recent = append_recent_turn("bad", build_recent_turn({"turn_count": "bad", "context_resolution": "bad"}, "assistant text"))
    assert_true(len(recent["recent_turns"]) == 1, "recent dialogue append should tolerate malformed existing dialogue")
    assert_true(recent["recent_turns"][0]["turn_id"] == 0, "recent turn count should be safe")


def test_semantic_failure_recovers_with_safe_plan_fallback() -> None:
    report = {
        "passed": False,
        "requires_retry": True,
        "semantic_judge": {"mode": "blocking", "issue_count": 1},
        "issues": [
            {
                "severity": "critical",
                "code": "semantic_profile_conflict",
                "message": "judge conflict",
                "retry": True,
                "detail": {"source": "semantic_judge", "mode": "blocking"},
            }
        ],
    }
    recovered = _safe_plan_fallback_for_semantic_failure(
        report,
        {
            "action_intent": "create",
            "domain": "workout",
            "proposed_plan_type": "workout",
            "user_profile": {"available_time_minutes": 8},
        },
    )
    assert_true(recovered is not None, "semantic-only failures should recover with safe fallback")
    assert_true(recovered["validation_report"]["passed"] is True, "semantic fallback should mark validation recovered")
    assert_true(recovered["generation_quality_flags"]["semantic_fallback_applied"] is True, "semantic fallback flag should be visible")
    assert_true(recovered["generation_quality_flags"]["semantic_fallback_revalidated"] is True, "semantic fallback should be revalidated")
    assert_true(
        recovered["generation_quality_flags"]["semantic_fallback_revalidation_issue_count"] >= 0,
        "semantic fallback should expose revalidation issue count for quality traces",
    )


def test_validator_handles_malformed_state_containers() -> None:
    malformed_state = {
        "response": "Plan proposal text with enough detail for validation.",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "diet",
        "proposed_plan_type": "diet",
        "proposed_plan": "bad",
        "profile_constraints": {
            "should_use_rag": True,
            "profile_field_coverage": {"present_count": "bad"},
            "retrieval_critical_constraints": "dairy_allergy",
        },
        "retrieval_decision": "bad",
        "search_results": [
            "drop",
            {
                "source": "external",
                "metadata": {"kb_id": "kb-1", "constraints": ["dairy_allergy"]},
                "text": "dairy allergy evidence",
            },
        ],
        "draft_components": "bad",
    }

    report = _validate_state(malformed_state)
    assert_true("quality_dimensions" not in report or isinstance(report.get("issues"), list), "validator should not crash on malformed containers")
    assert_true(
        _semantic_validation_mode(malformed_state) == "skip",
        "malformed profile coverage should not force semantic validation",
    )
    payload = json.loads(_semantic_validation_payload(malformed_state))
    assert_true(payload["search_result_count"] == 2, "semantic payload should preserve bounded list length")
    assert_true(len(payload["search_results_preview"]) == 1, "semantic payload should skip malformed search result entries")
    assert_true(
        payload["retrieval_evidence_contract"]["expected_critical_constraints"] == ["dairy_allergy"],
        "retrieval evidence contract should normalize scalar critical constraints",
    )

    dimensions = _validation_quality_dimensions(
        {
            "profile_constraints": "bad",
            "retrieval_decision": "bad",
            "search_results": "bad",
            "draft_components": "bad",
            "search_quality": "ok",
        },
        {"issues": ["drop"], "semantic_judge": "bad"},
    )
    assert_true(dimensions["profile_field_coverage"] == {}, "quality dimensions should normalize malformed profile coverage")
    assert_true(dimensions["semantic_judge"]["issue_count"] == 0, "quality dimensions should normalize malformed semantic judge")


def test_langsmith_quality_tracks_fallback_recovery() -> None:
    quality = evaluate_trace_quality(
        {
            "status": "response_sent",
            "kind": "chat",
            "response": {"response": "Here is a validated safe plan proposal."},
            "state_summary": {
                "intent": "plan",
                "action_intent": "create",
                "domain": "workout",
                "search_quality": "ok",
                "search_results_count": 1,
                "proposed_plan_count": 2,
                "pending_writes_count": 0,
                "pending_sequential_plan": {"domain": "diet", "reason": "mixed_workout_diet_sequence"},
                "needs_clarification": False,
                "generation_quality_flags": {
                    "semantic_fallback_applied": True,
                    "semantic_fallback_revalidated": True,
                    "semantic_fallback_recovered_codes": ["semantic_profile_conflict"],
                    "semantic_fallback_revalidation_issue_count": 0,
                },
            },
        }
    )
    issue_codes = {issue["code"] for issue in quality["issues"]}
    assert_true(
        quality["signals"]["pending_sequential_plan"]["domain"] == "diet",
        "LangSmith quality signals should retain pending sequential state",
    )
    assert_true(
        "semantic_fallback_recovery_used" in issue_codes,
        "LangSmith quality should show when deterministic fallback recovery was used",
    )


def test_langsmith_quality_tracks_evidence_and_profile_coverage() -> None:
    quality = evaluate_trace_quality(
        {
            "status": "response_sent",
            "kind": "chat",
            "response": {"response": "Here is a plan proposal with limited evidence."},
            "state_summary": {
                "intent": "plan",
                "action_intent": "create",
                "domain": "diet",
                "search_quality": "ok",
                "search_results_count": 0,
                "proposed_plan_count": 2,
                "pending_writes_count": 0,
                "needs_clarification": False,
                "validation_report": {
                    "quality_dimensions": {
                        "requires_external": True,
                        "evidence_status": "missing",
                        "profile_field_coverage": {"present_count": 2},
                    }
                },
            },
        }
    )
    issue_codes = {issue["code"] for issue in quality["issues"]}
    assert_true("required_evidence_missing" in issue_codes, "quality should flag missing required evidence")
    assert_true("low_profile_coverage_for_plan" in issue_codes, "quality should flag low profile coverage for plans")
    assert_true(quality["signals"]["requires_external"] is True, "quality signals should expose required evidence")
    assert_true(
        quality["signals"]["profile_field_coverage"]["present_count"] == 2,
        "quality signals should expose profile field coverage",
    )


def test_langsmith_quality_handles_malformed_numeric_signals() -> None:
    quality = evaluate_trace_quality(
        {
            "status": "response_sent",
            "kind": "home_recommendation",
            "response": "A safe concise response with enough text for evaluation.",
            "events": ["drop", {"stage": "home_recommendation.profile_guard.workout", "status": "ok"}],
            "state_summary": {
                "intent": "plan",
                "action_intent": "create",
                "domain": "workout",
                "search_quality": "ok",
                "search_results_count": "many",
                "proposed_plan_count": "bad",
                "pending_writes_count": "bad",
                "needs_clarification": False,
                "draft_components": "bad",
                "generation_quality_flags": "bad",
                "validation_report": {
                    "quality_dimensions": {
                        "requires_external": True,
                        "profile_field_coverage": {"present_count": "bad"},
                        "profile_fit_warning_codes": "bad",
                        "goal_fit_warning_codes": "bad",
                        "critical_profile_fit_codes": "bad",
                        "semantic_judge": {"mode": "observe", "issue_count": "bad"},
                    }
                },
            },
        }
    )
    signals = quality["signals"]
    assert_true(signals["search_results_count"] == 0, "malformed search count should normalize to zero")
    assert_true(signals["proposed_plan_count"] == 0, "malformed plan count should normalize to zero")
    assert_true(signals["pending_writes_count"] == 0, "malformed pending write count should normalize to zero")
    assert_true(signals["profile_fit_warning_codes"] == [], "malformed warning code lists should normalize to empty")
    issue_codes = {issue["code"] for issue in quality["issues"]}
    assert_true("required_evidence_missing" in issue_codes, "malformed zeroed retrieval count should still be evaluated")
    assert_true("missing_plan_proposal" in issue_codes, "malformed zeroed plan count should still be evaluated")


def test_langsmith_quality_tracks_goal_fit_warnings() -> None:
    validation_report = {
        "passed": True,
        "issues": [
            {
                "severity": "warning",
                "code": "fat_loss_goal_without_cardio_signal",
                "message": "missing cardio",
                "retry": False,
            },
            {
                "severity": "warning",
                "code": "mobility_goal_without_mobility_signal",
                "message": "missing mobility",
                "retry": False,
            },
        ],
    }
    dimensions = _validation_quality_dimensions(
        {
            "action_intent": "create",
            "profile_constraints": {"goals": ["fat_loss", "mobility"]},
            "retrieval_decision": {"requires_external": False},
            "search_quality": "ok",
            "search_results": [],
        },
        validation_report,
    )
    assert_true(dimensions["profile_fit_warning_count"] == 2, "validation dimensions should count profile-fit warnings")
    assert_true(dimensions["goal_fit_warning_count"] == 2, "validation dimensions should count goal-fit warnings")

    quality = evaluate_trace_quality(
        {
            "status": "response_sent",
            "kind": "chat",
            "response": {"response": "운동 플랜을 제안했어요."},
            "state_summary": {
                "intent": "plan",
                "action_intent": "create",
                "domain": "workout",
                "search_quality": "ok",
                "search_results_count": 0,
                "proposed_plan_count": 1,
                "pending_writes_count": 0,
                "needs_clarification": False,
                "validation_report": {"quality_dimensions": dimensions},
            },
        }
    )
    issue_codes = {issue["code"] for issue in quality["issues"]}
    assert_true("profile_fit_warning" in issue_codes, "LangSmith quality should expose profile-fit warnings")
    assert_true("goal_fit_warning" in issue_codes, "LangSmith quality should expose goal-fit warnings")
    assert_true(
        quality["signals"]["goal_fit_warning_codes"] == [
            "fat_loss_goal_without_cardio_signal",
            "mobility_goal_without_mobility_signal",
        ],
        "LangSmith quality signals should expose goal-fit warning codes",
    )


def test_plan_output_omits_constraint_exposition() -> None:
    plan = [
        {
            "name": "Breakfast",
            "detail": "두유 오트밀 + 바나나 + 삶은 달걀 / 유제품 알레르기 고려 / 감량 목표 반영",
            "day": "2026-05-18",
            "ex_list": [],
        }
    ]
    components = normalize_draft_components(
        {
            "core_message": "식단안을 제안할게요.",
            "reason_points": ["유제품 알레르기를 고려해 대체 식품을 넣었어요."],
            "suggested_action": "필요하면 제약을 더 알려주세요.",
            "search_grounding_summary": "사용자 제약을 반영했어요.",
            "safety_notes": ["알레르기/식이 제약(유제품)은 제외하고 안전한 대체 식품으로 바꾸세요."],
            "approval_question": "이 식단 플랜으로 작성할까요?",
        }
    )
    components["plan_preview"] = _render_plan_preview_from_items(plan)
    minimized = _minimize_plan_exposition(components, "diet")
    text = render_draft_preview(minimized)

    assert_true("두유 오트밀" in text, "concrete meal should remain visible")
    assert_true("이 식단 플랜으로 작성할까요?" in text, "approval question should remain visible")
    assert_true(not any(token in text for token in ("알레르기", "고려", "제외", "대체", "반영")), "plan response should omit constraint exposition")

    payloads = to_plan_create_batches({"has_plan": True, "plan_type": "diet", "items": plan})
    stored_detail = payloads[0]["items"][0]["detail"]
    assert_true(not any(token in stored_detail for token in ("알레르기", "고려", "제외", "대체", "반영")), "stored diet detail should stay concrete")


def test_diet_payload_stores_food_only() -> None:
    plan = [
        {
            "name": "Lunch",
            "detail": "현미밥, 닭가슴살, 데친 채소 (감량 목표 반영) - 혈당 안정 고려",
            "day": "2026-05-18",
            "ex_list": [],
        }
    ]

    payloads = to_plan_create_batches({"has_plan": True, "plan_type": "diet", "items": plan})
    stored_detail = payloads[0]["items"][0]["detail"]

    assert_true("현미밥" in stored_detail and "닭가슴살" in stored_detail, "stored diet detail should keep concrete foods")
    assert_true(not any(token in stored_detail for token in ("목표", "반영", "혈당", "고려")), "stored diet detail should remove rationale text")
    assert_true(len(stored_detail) <= 80, "stored diet detail should stay compact for calendar display")


def test_week_workout_plan_expands_from_one_day_request() -> None:
    today = date.fromisoformat(kst_today_iso())
    base_plan = [
        {
            "name": "전신 루틴",
            "detail": "스쿼트, 푸쉬업",
            "day": today.isoformat(),
            "ex_list": [{"exercise_name": "스쿼트", "sets": 2, "calories": 60}],
        },
        {
            "name": "유산소 루틴",
            "detail": "빠른 걷기",
            "day": today.isoformat(),
            "ex_list": [{"exercise_name": "빠른 걷기", "duration_minutes": 18, "calories": 90}],
        },
        {
            "name": "하체 루틴",
            "detail": "홈트 의자 스쿼트",
            "day": today.isoformat(),
            "ex_list": [{"exercise_name": "홈트 의자 스쿼트", "sets": 3, "calories": 70}],
        },
        {
            "name": "상체 루틴",
            "detail": "푸쉬업, 밴드 로우",
            "day": today.isoformat(),
            "ex_list": [{"exercise_name": "푸쉬업", "sets": 3, "calories": 60}],
        },
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "일주일 운동 플랜을 내 상태에 맞춰서 짜줘"},
        base_plan,
        "workout",
    )
    days = sorted({item["day"] for item in expanded})

    assert_true(len(expanded) == 7, "one-week workout plan should show every calendar day including recovery days")
    assert_true(
        days
        == [
            today.isoformat(),
            (today + timedelta(days=1)).isoformat(),
            (today + timedelta(days=2)).isoformat(),
            (today + timedelta(days=3)).isoformat(),
            (today + timedelta(days=4)).isoformat(),
            (today + timedelta(days=5)).isoformat(),
            (today + timedelta(days=6)).isoformat(),
        ],
        "one-week workout plan should cover the whole week",
    )


def test_seven_day_diet_plan_expands_by_calendar_day() -> None:
    today = date.fromisoformat(kst_today_iso())
    base_plan = [
        {"name": "Breakfast", "detail": "오트밀, 두유, 바나나", "day": today.isoformat(), "ex_list": []},
        {"name": "Lunch", "detail": "현미밥, 닭가슴살, 채소", "day": today.isoformat(), "ex_list": []},
        {"name": "Dinner", "detail": "두부 샐러드, 고구마", "day": today.isoformat(), "ex_list": []},
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "7일 식단 플랜 짜줘"},
        base_plan,
        "diet",
    )
    days = {item["day"] for item in expanded}

    assert_true(len(days) == 7, "seven-day diet plan should cover seven calendar days")
    assert_true(len(expanded) == 21, "seven-day diet plan should repeat meal slots for each day")


def test_weekly_diet_plan_expands_for_real_korean_chi_request() -> None:
    today = date.fromisoformat(kst_today_iso())
    base_plan = [
        {"name": "\uc544\uce68", "detail": "\ud604\ubbf8\uc8fd, \ube14\ub8e8\ubca0\ub9ac, \uc0b6\uc740 \ub2ed\uac00\uc2b4\uc0b4", "day": today.isoformat(), "ex_list": []},
        {"name": "\uc810\uc2ec", "detail": "\ud604\ubbf8\ubc25, \ub450\ubd80 \uc2a4\ud14c\uc774\ud06c, \uad6c\uc6b4 \ucc44\uc18c", "day": today.isoformat(), "ex_list": []},
        {"name": "\uc800\ub141", "detail": "\ub450\ubd80 \ucc44\uc18c\ubcf6\uc74c, \uace0\uad6c\ub9c8, \ucc44\uc18c", "day": today.isoformat(), "ex_list": []},
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "\uc77c\uc8fc\uc77c\uce58 \uc2dd\ub2e8 \uc9dc\uc918"},
        base_plan,
        "diet",
    )
    days = sorted({item["day"] for item in expanded})

    assert_true(len(days) == 7, "weekly diet request with real Korean should cover seven calendar days")
    assert_true(len(expanded) == 21, "weekly diet request should create three meals for each day")
    assert_true(days[0] == today.isoformat(), "weekly diet request should start today by default")
    assert_true(days[-1] == (today + timedelta(days=6)).isoformat(), "weekly diet request should end after seven days")


def test_weekly_diet_preview_groups_by_calendar_day() -> None:
    today = date.fromisoformat(kst_today_iso())
    expanded = []
    for offset in range(7):
        current_day = (today + timedelta(days=offset)).isoformat()
        expanded.extend(
            [
                {"name": "\uc544\uce68", "detail": "\ud604\ubbf8\uc8fd, \ube14\ub8e8\ubca0\ub9ac", "day": current_day, "ex_list": []},
                {"name": "\uc810\uc2ec", "detail": "\ud604\ubbf8\ubc25, \ub450\ubd80", "day": current_day, "ex_list": []},
                {"name": "\uc800\ub141", "detail": "\uace0\uad6c\ub9c8, \ucc44\uc18c\ubcf6\uc74c", "day": current_day, "ex_list": []},
            ]
        )

    preview = _render_plan_preview_from_items(expanded)

    assert_true(preview.count("\n") == 6, "weekly diet preview should show one compact row per day")
    assert_true(today.isoformat() in preview, "weekly diet preview should include the first day")
    assert_true((today + timedelta(days=6)).isoformat() in preview, "weekly diet preview should include the seventh day")
    assert_true("\uc678 " not in preview, "weekly diet preview should not hide seven-day plans behind a remainder line")


def test_weekly_workout_plan_expands_for_real_korean_chi_request() -> None:
    today = date.fromisoformat(kst_today_iso())
    base_plan = [
        {
            "name": "\uac00\ubcbc\uc6b4 \uc804\uc2e0 \ub8e8\ud2f4",
            "detail": "\uc758\uc790 \uc2a4\ucffc\ud2b8, \ubcbd \ud478\uc2dc\uc5c5, \uc804\uc2e0 \uc2a4\ud2b8\ub808\uce6d",
            "day": today.isoformat(),
            "ex_list": [
                {"exercise_name": "\uc758\uc790 \uc2a4\ucffc\ud2b8", "sets": 2},
                {"exercise_name": "\ubcbd \ud478\uc2dc\uc5c5", "sets": 2},
                {"exercise_name": "\uc804\uc2e0 \uc2a4\ud2b8\ub808\uce6d", "sets": 2},
            ],
        },
        {
            "name": "\uac00\ubcbc\uc6b4 \uc720\uc0b0\uc18c",
            "detail": "\ud3b8\uc548\ud55c \uac77\uae30",
            "day": today.isoformat(),
            "ex_list": [{"exercise_name": "\ud3b8\uc548\ud55c \uac77\uae30", "duration_minutes": 15}],
        },
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "\uc77c\uc8fc\uc77c\uce58 \uc6b4\ub3d9 \uc9dc\uc918"},
        base_plan,
        "workout",
    )
    days = sorted({item["day"] for item in expanded})
    preview = _render_plan_preview_from_items(expanded)

    assert_true(len(days) == 7, "weekly workout request with real Korean should cover seven calendar days")
    assert_true(len(expanded) == 7, "weekly workout request should include workout/recovery entries for every day")
    assert_true(days[0] == today.isoformat(), "weekly workout request should start today by default")
    assert_true(days[-1] == (today + timedelta(days=6)).isoformat(), "weekly workout request should end after seven days")
    assert_true(preview.count("\n") == 6, "weekly workout preview should show all seven days")


def test_week_plan_without_start_date_aligns_to_today() -> None:
    today = date.fromisoformat(kst_today_iso())
    future_start = today + timedelta(days=3)
    base_plan = [
        {
            "name": f"{offset + 1}일차 루틴",
            "detail": "가벼운 운동",
            "day": (future_start + timedelta(days=offset)).isoformat(),
            "ex_list": [{"exercise_name": "가벼운 걷기", "duration_minutes": 20, "calories": 80}],
        }
        for offset in range(7)
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "일주일 운동 플랜 짜줘"},
        base_plan,
        "workout",
    )
    days = sorted({item["day"] for item in expanded})

    assert_true(days[0] == today.isoformat(), "weekly plan without explicit start date should start today")
    assert_true(days[-1] == (today + timedelta(days=6)).isoformat(), "weekly plan should preserve the seven-day span")


def test_weekly_workout_preview_shows_all_seven_days() -> None:
    today = date.fromisoformat(kst_today_iso())
    plan = [
        {
            "name": f"{offset + 1}일차 루틴",
            "detail": "가벼운 운동",
            "day": (today + timedelta(days=offset)).isoformat(),
            "ex_list": [{"exercise_name": "가벼운 걷기", "duration_minutes": 20, "calories": 80}],
        }
        for offset in range(7)
    ]

    preview = _render_plan_preview_from_items(plan)

    assert_true(preview.count("\n") == 6, "seven-day workout preview should show all seven rows")
    assert_true("외 " not in preview, "seven-day workout preview should not hide days behind a remainder line")


def test_modify_fallback_reuses_active_proposal() -> None:
    today = date.fromisoformat(kst_today_iso())
    state = {
        "user_message": "일주일 운동 플랜이라니까? 왜 5월 22일만 추천해?",
        "intent": "수정",
        "modify_target": "workout",
        "active_proposal": {
            "domain": "workout",
            "write_mode": "create",
            "items": [
                {
                    "name": "상체 루틴",
                    "detail": "푸쉬업",
                    "day": today.isoformat(),
                    "ex_list": [{"exercise_name": "푸쉬업", "sets": 3, "calories": 60}],
                }
            ],
        },
    }

    components, _draft_text, proposed_plan, plan_type, action = _build_modify_plan_fallback(state)

    assert_true(plan_type == "workout", "modify fallback should keep the active proposal domain")
    assert_true(action == "update", "modify fallback should mark the proposal as an update")
    assert_true(len(proposed_plan) == 1 and proposed_plan[0]["name"] == "상체 루틴", "modify fallback should reuse active proposal items")
    assert_true("수정" in components["approval_question"], "modify fallback should ask for update approval")


def test_month_diet_plan_expands_by_calendar_day() -> None:
    today = date.fromisoformat(kst_today_iso())
    base_plan = [
        {"name": "Breakfast", "detail": "오트밀, 두유, 바나나", "day": today.isoformat(), "ex_list": []},
        {"name": "Lunch", "detail": "현미밥, 닭가슴살, 채소", "day": today.isoformat(), "ex_list": []},
        {"name": "Dinner", "detail": "두부 샐러드, 고구마", "day": today.isoformat(), "ex_list": []},
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "한 달 식단 플랜 짜줘"},
        base_plan,
        "diet",
    )
    days = {item["day"] for item in expanded}

    assert_true(len(days) == 30, "one-month diet plan should cover 30 calendar days")
    assert_true(len(expanded) == 90, "one-month diet plan should repeat meal slots for each day")
    assert_true(expanded[0]["day"] == today.isoformat(), "expansion should start from today when no start date is explicit")


def test_month_workout_plan_expands_weekly_sessions() -> None:
    today = date.fromisoformat(kst_today_iso())
    base_plan = [
        {
            "name": "상체 루틴",
            "detail": "푸쉬업과 로우",
            "day": today.isoformat(),
            "ex_list": [{"exercise_name": "푸쉬업", "sets": 3, "calories": 60}],
        },
        {
            "name": "하체 루틴",
            "detail": "스쿼트와 브릿지",
            "day": (today + timedelta(days=1)).isoformat(),
            "ex_list": [{"exercise_name": "스쿼트", "sets": 3, "calories": 70}],
        },
        {
            "name": "유산소 루틴",
            "detail": "빠른 걷기",
            "day": (today + timedelta(days=2)).isoformat(),
            "ex_list": [{"exercise_name": "빠른 걷기", "duration_minutes": 20, "calories": 90}],
        },
    ]

    expanded = _expand_long_range_plan_if_requested(
        {"user_message": "4주 운동 플랜 짜줘"},
        base_plan,
        "workout",
    )
    days = sorted({item["day"] for item in expanded})

    assert_true(len(expanded) == 12, "four-week workout plan should repeat weekly sessions")
    assert_true(days[0] == today.isoformat(), "workout expansion should start from today when no start date is explicit")
    assert_true(days[-1] >= (today + timedelta(days=23)).isoformat(), "workout expansion should span the requested period")


def test_demo_plan_rag_degraded_does_not_fail_closed() -> None:
    constraints = {
        "should_use_rag": True,
        "hard_profile_constraints": ["dairy_allergy", "knee_pain"],
        "retrieval_critical_constraints": ["dairy_allergy"],
    }
    plan_state = {
        "action_intent": "create",
        "retrieval_decision": {"requires_external": True},
    }
    info_state = {
        "action_intent": "info",
        "retrieval_decision": {"requires_external": True},
    }

    assert_true(
        not _requires_external_fail_closed(plan_state, constraints),
        "demo plan creation should warn, not block, when external RAG is temporarily degraded",
    )
    assert_true(
        _requires_external_fail_closed(info_state, constraints),
        "specialized info answers should still fail closed when required external evidence is unavailable",
    )

    report = _validate_state(
        {
            **plan_state,
            "response": "식단 플랜을 제안해요.",
            "intent": "계획",
            "domain": "diet",
            "proposed_plan_type": "diet",
            "proposed_plan": [
                {"name": "점심", "detail": "현미밥, 두부 스테이크, 채소", "day": kst_today_iso(), "ex_list": []}
            ],
            "profile_constraints": constraints,
            "search_quality": "degraded",
        }
    )
    dimensions = _validation_quality_dimensions(
        {
            **plan_state,
            "profile_constraints": constraints,
            "search_quality": "degraded",
            "search_results": [],
        },
        report,
    )
    assert_true(dimensions["evidence_status"] == "degraded_fail_open", "plan RAG degradation should be tracked explicitly")


def test_high_risk_plan_rag_degraded_fails_closed() -> None:
    constraints = {
        "should_use_rag": True,
        "hard_profile_constraints": ["kidney_disease"],
        "retrieval_critical_constraints": ["kidney_disease"],
        "critical_constraints": ["kidney_disease"],
    }
    state = {
        "action_intent": "create",
        "retrieval_decision": {"requires_external": True},
        "profile_constraints": constraints,
        "search_quality": "degraded",
    }

    assert_true(
        _requires_external_fail_closed(state, constraints),
        "high-risk medical diet plans should not fail open when required RAG is degraded",
    )
    assert_true(
        _semantic_validation_mode(
            {
                **state,
                "response": "식단 플랜을 제안해요.",
                "proposed_plan": [
                    {"name": "점심", "detail": "현미밥과 채소", "day": kst_today_iso(), "ex_list": []}
                ],
            }
        )
        == "blocking",
        "high-risk plan flows should use blocking semantic validation",
    )


def test_validator_blocks_high_risk_diet_conflicts() -> None:
    state = {
        "response": "식단 플랜을 제안해요.",
        "intent": "계획",
        "action_intent": "create",
        "domain": "diet",
        "needs_clarification": False,
        "proposed_plan_type": "diet",
        "proposed_plan": [
            {
                "name": "Lunch",
                "detail": "고단백 프로틴 쉐이크와 닭가슴살 200g",
                "day": kst_today_iso(),
                "ex_list": [],
            }
        ],
        "profile_constraints": {
            "hard_profile_constraints": ["kidney_disease"],
            "profile_field_coverage": {"present_count": 6},
        },
        "retrieval_decision": {"requires_external": False},
        "search_quality": "ok",
    }
    report = _validate_state(state)

    assert_true(
        any(issue.get("code") == "kidney_high_protein_conflict" for issue in report.get("issues") or []),
        "kidney disease profile should block obvious high-protein diet conflicts",
    )


def test_invalid_llm_plan_contract_triggers_fallback() -> None:
    invalid_workout = [
        {
            "name": "Routine",
            "detail": "No structured exercises",
            "day": kst_today_iso(),
            "ex_list": [],
        }
    ]
    valid_diet = [
        {
            "name": "Lunch",
            "detail": "Brown rice, tofu, vegetables",
            "day": kst_today_iso(),
            "ex_list": [],
        }
    ]

    assert_true(
        _plan_contract_needs_fallback(invalid_workout, "workout"),
        "workout drafts without exercise entries should be replaced before validation blocks the proposal",
    )
    assert_true(
        not _plan_contract_needs_fallback(valid_diet, "diet"),
        "valid diet drafts should not be replaced",
    )


def test_modify_without_active_plan_creates_new_proposal() -> None:
    components, draft_text, plan, plan_type, action = _build_modify_plan_fallback(
        {
            "user_message": "이번 주 운동을 3일로 줄여줘",
            "modify_target": "workout",
            "domain": "workout",
            "active_proposal": None,
            "user_profile": {},
        }
    )

    assert_true(plan_type == "workout", "explicit workout modify without active proposal should keep workout domain")
    assert_true(action == "create", "modify without active proposal should create a fresh proposal")
    assert_true(len(plan) >= 1, "modify without active proposal should synthesize a proposal")
    assert_true(bool(components.get("approval_question")), "fresh proposal should ask for approval")
    assert_true(bool(draft_text), "fresh proposal should render a response")


def test_demo_plan_semantic_judge_blocks_profile_sensitive_plans() -> None:
    plan_state = {
        "action_intent": "create",
        "response": "운동 플랜을 제안해요.",
        "proposed_plan": [
            {
                "name": "Light routine",
                "detail": "Low impact",
                "day": kst_today_iso(),
                "ex_list": [{"exercise_name": "Walk", "duration_minutes": 20}],
            }
        ],
        "profile_constraints": {
            "hard_profile_constraints": ["knee_pain", "dairy_allergy"],
            "profile_field_coverage": {"present_count": 8},
        },
        "retrieval_decision": {"requires_external": True},
    }
    info_state = {
        **plan_state,
        "action_intent": "info",
        "proposed_plan": [],
    }

    assert_true(
        _should_run_semantic_validation(plan_state),
        "structured plan flows should run the semantic judge for observability",
    )
    assert_true(
        _semantic_validation_mode(plan_state) == "blocking",
        "profile-sensitive plan semantic judge should be blocking so generator mistakes cannot pass through",
    )
    assert_true(
        _should_run_semantic_validation(info_state),
        "non-plan specialized answers should still use the semantic judge",
    )
    assert_true(
        _semantic_validation_mode(info_state) == "blocking",
        "non-plan specialized answers should keep blocking semantic validation",
    )


def test_explicit_workout_overrides_wrong_draft_plan_type() -> None:
    class WrongDraftType:
        proposed_plan_type = "diet"

    resolved = _resolve_proposed_plan_type(
        {"user_message": "스트레칭 위주 운동 플랜 작성해줘"},
        WrongDraftType(),
        [
            {
                "name": "스트레칭 루틴",
                "detail": "가벼운 회복 운동",
                "day": kst_today_iso(),
                "ex_list": [{"exercise_name": "전신 스트레칭", "sets": 2}],
            }
        ],
    )
    assert_true(resolved == "workout", "explicit workout request and exercise items should override wrong draft diet label")

    components = _normalize_plan_core_message(
        normalize_draft_components({"core_message": "식단 플랜을 제안해요."}),
        resolved,
        "create",
    )
    assert_true(components["core_message"] == "운동 플랜을 제안해요.", "core message should follow resolved plan type")

    render_state = _response_render_state(
        {"intent": "계획", "domain": "diet", "user_message": "스트레칭 위주 운동 플랜 작성해줘"},
        {"proposed_plan_type": "workout", "proposed_plan_action": "create"},
    )
    assert_true(render_state["domain"] == "workout", "persona renderer should use resolved payload domain")


def test_safe_diet_fallback_respects_compound_allergies() -> None:
    fallback = _safe_diet_fallback_for_validation_failure(
        {
            "passed": False,
            "requires_retry": False,
            "issues": [
                {"severity": "critical", "code": "allergen_conflict", "message": "allergen", "retry": False}
            ],
        },
        {
            "action_intent": "create",
            "domain": "diet",
            "proposed_plan_type": "diet",
            "user_message": "soy, dairy, nut, egg 없이 채식 식단 작성해줘",
            "user_profile": {
                "diet_type": "vegetarian",
                "allergies": ["soy", "dairy", "nut", "egg"],
            },
        },
    )
    assert_true(fallback is not None, "recoverable diet conflicts should get deterministic safe fallback")
    assert_true(
        fallback["generation_quality_flags"]["safe_diet_fallback_revalidated"] is True,
        "safe diet fallback should pass deterministic revalidation before returning",
    )
    text = " ".join(item["detail"] for item in fallback["proposed_plan"])
    assert_true(not any(token in text for token in ("두부", "두유", "견과", "달걀", "요거트")), "fallback should avoid compound allergy terms")
    assert_true("렌틸콩" in text or "병아리콩" in text, "fallback should still contain concrete plant protein")


def test_pending_writes_are_bounded_and_dead_lettered() -> None:
    writes = [
        {"write_type": "profile", "payload": {"nickname": f"user-{index}"}, "write_id": f"write-{index}"}
        for index in range(30)
    ]
    normalized = _normalize_pending_writes(writes, current_turn=1)
    assert_true(len(normalized) == 24, "session pending writes should be capped")
    assert_true(normalized[0]["write_id"] == "write-6", "cap should keep the most recent pending writes")

    failed = normalized[0]
    for turn in range(1, 5):
        failed = _mark_pending_write_failed(failed, turn, RuntimeError("WAS unavailable"))
    assert_true(_pending_write_exhausted(failed), "repeated session replay failures should move to outbox-only handling")
    malformed_failed = _mark_pending_write_failed(
        {"write_type": "profile", "payload": {"nickname": "bad"}, "attempt_count": {"bad": 1}},
        3,
        RuntimeError("e" * 900),
    )
    assert_true(malformed_failed["attempt_count"] == 1, "malformed pending write attempts should restart safely")
    assert_true(len(malformed_failed["last_error"]) == 500, "pending write failure errors should be bounded")
    assert_true(
        not _pending_write_exhausted({"attempt_count": ["bad"]}),
        "malformed pending write attempts should not be treated as exhausted",
    )
    assert_true(
        not _pending_write_waiting_for_retry({"next_retry_turn": {"bad": 1}}, 3),
        "malformed pending write retry turns should not block replay",
    )

    generated_key_writes = [
        {
            "write_type": "profile",
            "payload": {"nickname": "same", "bio": "x" * 2000},
            "attempt_count": "bad",
            "next_retry_turn": "-99",
            "last_error": "e" * 2000,
        },
        {
            "write_type": "profile",
            "payload": {"bio": "x" * 2000, "nickname": "same"},
        },
        {
            "write_type": "",
            "payload": {"nickname": "drop"},
        },
        {
            "write_type": "profile",
            "payload": "drop",
        },
    ]
    normalized_generated = _normalize_pending_writes(generated_key_writes, current_turn=3)
    assert_true(len(normalized_generated) == 1, "generated pending write keys should dedupe stable payloads and drop invalid writes")
    assert_true(len(normalized_generated[0]["write_id"]) <= 160, "generated pending write key should be bounded")
    assert_true(normalized_generated[0]["attempt_count"] == 0, "pending write attempt_count should be normalized")
    assert_true(normalized_generated[0]["next_retry_turn"] == 3, "pending write next_retry_turn should not be earlier than current turn")
    assert_true(len(normalized_generated[0]["last_error"]) == 500, "pending write last_error should be bounded")

    merged = _merge_pending_writes(
        [{"write_type": "profile", "payload": {"nickname": "same"}, "attempt_count": -2}],
        [{"write_type": "profile", "payload": {"nickname": "same"}, "attempt_count": 5}],
    )
    assert_true(len(merged) == 1, "chat pending write merge should dedupe generated keys")
    assert_true(merged[0]["attempt_count"] == 0, "chat pending write merge should normalize negative attempts")


def test_chat_router_summarizes_malformed_state_safely() -> None:
    malformed_state = {
        "effective_user_profile": "bad",
        "user_profile": "bad",
        "pending_writes": "bad",
        "search_results": "bad",
        "proposed_plan": "bad",
        "recent_dialogue": "bad",
        "active_proposal": "bad",
    }
    debug = _build_debug_state("trace-1", malformed_state)
    summary = _build_state_summary(malformed_state)
    assert_true(debug["search_results_count"] == 0, "debug state should not count characters as search results")
    assert_true(debug["proposed_plan_count"] == 0, "debug state should not count characters as plans")
    assert_true(debug["pending_writes_count"] == 0, "debug state should not count characters as writes")
    assert_true(summary["recent_dialogue_turns"] == 0, "state summary should tolerate malformed recent dialogue")

    resolved_plan, plan_type, action = _resolve_plan_write_fields(
        {
            "proposed_plan": "bad",
            "proposed_plan_type": "workout",
            "proposed_plan_action": "create",
            "active_proposal": "bad",
        }
    )
    assert_true(resolved_plan is None and plan_type == "workout" and action == "create", "plan write resolver should reject malformed plan lists")

    merged = _merge_pending_writes(
        "bad",
        [
            {"write_type": "profile", "payload": {"nickname": "ok"}},
            "drop",
        ],
    )
    assert_true(len(merged) == 1 and merged[0]["write_type"] == "profile", "pending write merge should ignore malformed containers")

    status = _was_write_status(None, mode="fallback", state={"pending_writes": "bad"})
    assert_true(status["pending_count"] == 0, "WAS write status should ignore malformed pending writes")


def test_today_plan_normalization_bounds_malformed_was_payload() -> None:
    long_text = "x" * 500
    raw_plan = [
        "drop",
        {
            "id": long_text,
            "type": "exercise",
            "name": "cardio",
            "detail": long_text,
            "day": long_text,
            "ex_list": [
                "drop",
                {"exercise_name": long_text, "sets": 2},
            ],
        },
        {
            "type": "meal",
            "name": "breakfast",
            "detail": "oats",
            "ex_list": "bad",
        },
    ] + [{"id": f"id-{index}", "type": "meal", "name": "lunch", "detail": "rice"} for index in range(90)]

    normalized = _normalize_today_plan(raw_plan)

    assert_true(len(normalized) == 79, "today_plan should drop malformed rows and cap before expanding state")
    assert_true(len(normalized[0]["id"]) == 160, "today_plan text fields should be bounded")
    assert_true(len(normalized[0]["detail"]) == 160, "today_plan detail should be bounded")
    assert_true(len(normalized[0]["ex_list"]) == 1, "today_plan exercises should drop malformed entries")
    assert_true(
        len(normalized[0]["ex_list"][0]["exercise_name"]) == 120,
        "today_plan exercise names should be bounded",
    )
    assert_true(normalized[1]["ex_list"] == [], "malformed today_plan ex_list should normalize to empty list")
    assert_true(_normalize_today_plan("bad") == [], "non-list today_plan payload should not enter graph state")


def test_record_and_preprocess_tolerate_malformed_write_state() -> None:
    assert_true(_normalize_user_profile("bad")["allergies"] == [], "preprocess profile normalization should ignore malformed profiles")
    assert_true(merge_profile_override_for_plan_context("bad", "bad") == {}, "profile merge should ignore malformed profile containers")
    assert_true(
        all(value in ("", ()) for value in canonical_profile_context("bad").values()),
        "canonical profile context should treat malformed profile as empty",
    )

    profile_result = asyncio.run(
        _handle_profile(
            {
                "user_profile": "bad",
                "effective_user_profile": ["bad"],
                "pending_profile_overlay": "bad",
                "profile_changes": {"weight": 70, "goal": "mobility"},
            }
        )
    )
    assert_true(profile_result["effective_user_profile"]["weight"] == 70, "record profile should apply valid changes over malformed profile state")
    assert_true(profile_result["pending_profile_overlay"]["goal"] == "mobility", "record profile should normalize malformed pending overlay")

    class FakeWas:
        async def get_today_plan(self, user_id):  # noqa: ANN001
            return ["drop", {"id": 123, "type": "exercise", "completed": False}]

    class FakeDeps:
        was = FakeWas()

    check_result = asyncio.run(
        _handle_plan_check(
            FakeDeps(),
            {
                "is_today": True,
                "user_id": "user-record-safe",
                "profile_changes": "bad",
                "today_plan": "bad",
                "user_message": "done",
            },
        )
    )
    assert_true(check_result["profile_changes"]["item_id"] == "123", "plan check should filter malformed plan rows and keep numeric ids")

    delete_result = asyncio.run(
        _handle_plan_delete(
            {
                "profile_changes": {
                    "target_dates": ["2026-06-03", "not-a-date", "2026-06-03"],
                    "plan_type": "unexpected",
                }
            }
        )
    )
    assert_true(delete_result["profile_changes"]["target_dates"] == ["2026-06-03"], "plan delete should keep only valid ISO dates")
    assert_true(delete_result["profile_changes"]["plan_type"] == "all", "plan delete should normalize unknown plan type")

    all_delete_result = asyncio.run(
        _handle_plan_delete(
            {
                "user_message": "\ud604\uc7ac \uce98\ub9b0\ub354\uc758 \ubaa8\ub4e0 \uc6b4\ub3d9/\uc2dd\ub2e8 \ub0b4\uc5ed\uc744 \uc0ad\uc81c\ud574\uc918",
                "profile_changes": {},
            }
        )
    )
    assert_true(all_delete_result["profile_changes"]["target_scope"] == "all", "calendar-wide delete should use all target scope")
    assert_true(all_delete_result["profile_changes"]["target_dates"] == [], "calendar-wide delete should not collapse to today's date")
    assert_true(all_delete_result["profile_changes"]["plan_type"] == "all", "calendar-wide workout/diet delete should delete both plan types")

    today_delete_result = asyncio.run(
        _handle_plan_delete(
            {
                "user_message": "\uc624\ub298 \uc6b4\ub3d9 \uc804\uccb4 \uc0ad\uc81c\ud574\uc918",
                "profile_changes": {},
            }
        )
    )
    assert_true(today_delete_result["profile_changes"]["target_scope"] == "dates", "date-qualified delete should stay date-scoped")
    assert_true(len(today_delete_result["profile_changes"]["target_dates"]) == 1, "date-qualified delete should keep one target date")

    draft = _build_plan_delete_draft({"profile_changes": all_delete_result["profile_changes"]})
    assert_true(
        "\ud604\uc7ac \uce98\ub9b0\ub354\uc758 \ubaa8\ub4e0" in draft["draft_components"]["core_message"],
        "calendar-wide delete response should name the full calendar scope",
    )


def test_outbox_reconcile_clears_succeeded_checkpoint_writes() -> None:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        db_path = tmp.name

    async def scenario() -> None:
        writes = [
            {"write_type": "profile", "payload": {"nickname": "done"}, "write_id": "write-done"},
            {"write_type": "profile", "payload": {"nickname": "pending"}, "write_id": "write-pending"},
            {"write_type": "", "payload": {"nickname": "drop"}, "write_id": "write-drop-type"},
            {"write_type": "profile", "payload": "drop", "write_id": "write-drop-payload"},
            {"payload": {"nickname": "drop"}, "write_id": "write-drop-missing-type"},
        ]
        await enqueue_was_outbox(
            db_path,
            user_id="user-1",
            session_id="session-1",
            trace_id="trace-1",
            writes=writes,
        )
        await mark_was_outbox_succeeded(db_path, "write-done")
        kept, resolved = await reconcile_pending_writes_with_outbox(db_path, writes)
        assert_true(resolved == ["write-done"], "succeeded outbox write id should be reported as resolved")
        assert_true([write["write_id"] for write in kept] == ["write-pending"], "only unresolved writes should remain")

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM was_outbox")
            count_row = await cursor.fetchone()
        assert_true(count_row[0] == 2, "invalid outbox writes should not be enqueued")

        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE was_outbox SET payload_json = 'not-json', status = 'pending' WHERE write_id = 'write-pending'")
            await db.commit()

        class FakeDeps:
            was = object()

        replayed = await replay_due_was_outbox(db_path, FakeDeps())
        assert_true(replayed["attempted"] == 1 and replayed["failed"] == 1, "invalid persisted JSON should fail one replay without crashing")
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT status, attempt_count FROM was_outbox WHERE write_id = 'write-pending'")
            status, attempt_count = await cursor.fetchone()
        assert_true(status == "pending" and attempt_count == 1, "invalid persisted JSON should remain pending with incremented attempt")

    try:
        asyncio.run(scenario())
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_finalize_mojibake_guard_can_repair_plan_response() -> None:
    state = {
        "proposed_plan_type": "diet",
        "proposed_plan": [
            {"name": "점심", "detail": "현미밥, 두부 스테이크, 채소", "day": kst_today_iso(), "ex_list": []}
        ],
    }
    repaired = _safe_response_from_state(state)
    assert_true(_looks_like_mojibake("怨꾪쉷 ?대룞"), "known broken text should be detected")
    assert_true(not _looks_like_mojibake(repaired or ""), "repaired response should not contain mojibake markers")
    assert_true("이 식단 플랜으로 작성할까요?" in (repaired or ""), "repaired plan response should keep approval UX")


def test_finalize_handles_malformed_response_containers() -> None:
    class FakeTrace:
        def record_current_event(self, **kwargs):  # noqa: ANN003
            self.last_event = kwargs

    class FakeDeps:
        trace = FakeTrace()

    deps = FakeDeps()
    node = make_finalize_node(deps)
    result = asyncio.run(
        node(
            {
                "response": {"bad": "container"},
                "draft_components": "bad",
                "draft_response": "clean draft fallback",
                "validation_report": "bad",
                "proposed_plan_type": "diet",
                "proposed_plan": ["bad"],
            }
        )
    )
    assert_true(result["response"] == "clean draft fallback", "finalize should ignore malformed response containers")
    assert_true(deps.trace.last_event["detail"]["validation_issue_count"] == 0, "malformed validation report should count as zero issues")
    assert_true(_safe_response_from_state({"proposed_plan_type": "diet", "proposed_plan": ["bad"]}) is None, "invalid plan preview should not produce an empty proposal")

    repaired = _safe_response_from_state(
        {
            "proposed_plan_type": "workout",
            "proposed_plan": [
                {"name": {"bad": "name"}, "detail": {"bad": "detail"}, "ex_list": "bad"},
                "bad",
            ],
        }
    )
    assert_true(repaired is not None and "{'bad'" not in repaired, "safe plan preview should not stringify malformed fields")


def test_dairy_free_replacement_is_not_allergen_conflict() -> None:
    state = {
        "response": "식단 플랜을 제안해요.",
        "intent": "계획",
        "action_intent": "create",
        "domain": "diet",
        "needs_clarification": False,
        "proposed_plan_type": "diet",
        "proposed_plan": [
            {
                "name": "Breakfast",
                "detail": "무가당 콩요거트, 바나나, 견과류",
                "day": kst_today_iso(),
                "ex_list": [],
            }
        ],
        "profile_constraints": {
            "hard_profile_constraints": ["dairy_allergy"],
            "profile_field_coverage": {"present_count": 6},
        },
        "retrieval_decision": {"requires_external": False, "should_search": False},
        "search_quality": "ok",
    }
    report = _validate_state(state)

    assert_true(
        not any(issue.get("code") == "allergen_conflict" for issue in report.get("issues") or []),
        "dairy-free substitutes such as soy yogurt should not be treated as dairy allergens",
    )


def test_diet_constraint_conflict_triggers_safe_fallback() -> None:
    profile = {
        "diet_type": "vegetarian",
        "allergies": ["dairy"],
        "dietary_restrictions": ["vegetarian", "dairy_allergy"],
    }
    bad_plan = [
        {
            "name": "Lunch",
            "detail": "닭가슴살 샐러드와 그릭 요거트",
            "day": kst_today_iso(),
            "ex_list": [],
        }
    ]
    safe_plan = [
        {
            "name": "Lunch",
            "detail": "두부 스테이크와 무가당 콩요거트",
            "day": kst_today_iso(),
            "ex_list": [],
        }
    ]

    assert_true(
        _diet_plan_requires_safe_fallback(bad_plan, profile),
        "vegetarian dairy-free profile should replace meat/dairy diet drafts",
    )
    assert_true(
        not _diet_plan_requires_safe_fallback(safe_plan, profile),
        "safe plant-based dairy-free replacements should be accepted",
    )
    assert_true(
        _diet_plan_requires_safe_fallback(
            [{"name": "Snack", "detail": "그릭 요거트", "day": kst_today_iso(), "ex_list": []}],
            {},
            "식단에서 유제품 빼고 다시 작성해줘",
        ),
        "request-level dairy exclusion should also trigger safe fallback",
    )


def test_goal_fit_warnings_are_profile_sensitive() -> None:
    workout_report = _validate_state(
        {
            "response": "운동 플랜입니다.",
            "intent": INTENT_PLAN,
            "action_intent": "create",
            "domain": "workout",
            "proposed_plan_type": "workout",
            "proposed_plan": [
                {
                    "name": "상체 루틴",
                    "detail": "덤벨 프레스",
                    "day": kst_today_iso(),
                    "ex_list": [{"exercise_name": "덤벨 프레스", "sets": 3, "calories": 60}],
                }
            ],
            "profile_constraints": {"goals": ["fat_loss", "mobility"], "summary": {}},
        }
    )
    workout_codes = {issue["code"] for issue in workout_report["issues"]}
    assert_true("fat_loss_goal_without_cardio_signal" in workout_codes, "fat-loss workout should expose missing cardio signal")
    assert_true("mobility_goal_without_mobility_signal" in workout_codes, "mobility workout should expose missing mobility signal")

    diet_report = _validate_state(
        {
            "response": "식단 플랜입니다.",
            "intent": INTENT_PLAN,
            "action_intent": "create",
            "domain": "diet",
            "proposed_plan_type": "diet",
            "proposed_plan": [
                {
                    "name": "아침",
                    "detail": "흰빵과 잼",
                    "day": kst_today_iso(),
                    "ex_list": [],
                }
            ],
            "profile_constraints": {"goals": ["muscle_gain", "glucose_control", "heart_health"], "summary": {}},
        }
    )
    diet_codes = {issue["code"] for issue in diet_report["issues"]}
    assert_true("muscle_gain_diet_without_protein_signal" in diet_codes, "muscle-gain diet should expose missing protein signal")
    assert_true("glucose_goal_without_stable_carb_signal" in diet_codes, "glucose-control diet should expose missing stable-carb signal")
    assert_true("heart_health_goal_without_low_sodium_signal" in diet_codes, "heart-health diet should expose missing low-sodium signal")


def test_validator_blocks_medical_diet_synonyms() -> None:
    report = _validate_state(
        {
            "response": "Diet plan proposal.",
            "intent": "plan",
            "action_intent": "create",
            "domain": "diet",
            "needs_clarification": False,
            "proposed_plan_type": "diet",
            "proposed_plan": [
                {
                    "name": "Breakfast",
                    "detail": "Whey protein shake, anchovy shellfish ramen, unpasteurized cheese, OMAD detox",
                    "day": kst_today_iso(),
                    "ex_list": [],
                }
            ],
            "profile_constraints": {
                "hard_profile_constraints": [
                    "kidney_disease",
                    "gout",
                    "pregnancy",
                    "eating_disorder_risk",
                ],
                "profile_field_coverage": {"present_count": 8},
            },
            "retrieval_decision": {"requires_external": False, "should_search": False},
            "search_quality": "ok",
        }
    )
    codes = {issue.get("code") for issue in report.get("issues") or []}
    assert_true("kidney_high_protein_conflict" in codes, "validator should catch kidney high-protein synonyms")
    assert_true("gout_purine_conflict" in codes, "validator should catch gout purine synonyms")
    assert_true("pregnancy_food_safety_conflict" in codes, "validator should catch pregnancy food-safety synonyms")
    assert_true(
        "eating_disorder_extreme_plan_conflict" in codes,
        "validator should catch eating-disorder/extreme-diet synonyms",
    )


def test_explicit_new_domain_ignores_active_proposal_context() -> None:
    resolution = _resolve_context(
        {
            "user_message": "\uc2dd\ub2e8\uc5d0\uc11c \uc720\uc81c\ud488 \ube7c\uace0 \ub2e4\uc2dc \uc791\uc131\ud574\uc918",
            "active_proposal": {
                "domain": "workout",
                "write_mode": "create",
                "items": [{"name": "Workout", "day": kst_today_iso(), "ex_list": []}],
            },
        }
    )

    assert_true(
        resolution["resolved_reference"] == "none",
        "explicit diet request should not be rewritten as a workout active-proposal follow-up",
    )
    assert_true(resolution["resolved_domain"] == "diet", "explicit diet request should keep diet domain")


def test_context_resolver_sanitizes_malformed_reference_state() -> None:
    reference_message = f"{_CONTEXT_REFERENCE_MARKERS[0]} continue"
    invalid_active = _resolve_context(
        {
            "user_message": reference_message,
            "active_proposal": {"domain": "bad"},
            "recent_dialogue": "bad",
        }
    )
    assert_true(invalid_active["ambiguous"] is True, "invalid active proposal follow-up should become ambiguous instead of crashing")
    assert_true(invalid_active["resolved_text"] == reference_message, "invalid active proposal should preserve user text")

    malformed_recent = _resolve_context(
        {
            "user_message": f"{_CONTEXT_QUESTION_MARKERS[0]} why",
            "active_proposal": None,
            "recent_dialogue": {"recent_turns": ["drop"]},
        }
    )
    assert_true(malformed_recent["ambiguous"] is True, "malformed recent turn references should become ambiguous")
    assert_true(malformed_recent["resolved_text"], "malformed recent turn references should preserve resolved text")

    invalid_domain_recent = _resolve_context(
        {
            "user_message": f"{_CONTEXT_QUESTION_MARKERS[0]} why",
            "recent_dialogue": {"recent_turns": [{"domain": "bad"}]},
        }
    )
    assert_true(invalid_domain_recent["resolved_domain"] == "general", "recent turn domains should be normalized")


def test_home_recommendation_prompt_covers_profile_edges() -> None:
    prompt = build_home_recommendation_prompt_input(
        date="2026-06-01",
        scope="diet",
        user_profile={
            "age": 42,
            "activityLevel": "low",
            "fitness_level": "beginner",
            "frequency_per_week": 2,
            "foods_to_avoid": ["grapefruit"],
            "otherAllergy": "sesame",
            "conditions": ["hypertension"],
            "pain_points": ["knee"],
            "personality": "quiet solo",
            "selected_ai_persona": "daily_manager",
        },
        today_plan=[],
    )
    profile_json = prompt.split("[USER_PROFILE]\n", 1)[1].split("\n\n", 1)[0]
    profile = json.loads(profile_json)
    assert_true(profile["activityLevel"] == "low", "home prompt should retain activityLevel")
    assert_true(profile["activity_level"] == "low", "home prompt should expose normalized activity_level")
    assert_true(profile["exercise_level"] == "beginner", "home prompt should normalize fitness_level")
    assert_true(profile["exercise_frequency"] == 2, "home prompt should normalize frequency_per_week")
    assert_true(profile["foods_to_avoid"] == ["grapefruit"], "home prompt should include foods_to_avoid")
    assert_true(profile["otherAllergy"] == "sesame", "home prompt should include otherAllergy")
    assert_true(profile["conditions"] == ["hypertension"], "home prompt should include conditions")
    assert_true(profile["selected_ai_persona"] == "daily_manager", "home prompt should include selected persona")


def test_home_prompt_bounds_plan_and_recent_inputs() -> None:
    long_text = "x" * 500
    prompt = build_home_recommendation_prompt_input(
        date="2026-06-01",
        scope="all",
        user_profile={},
        today_plan=[
            "drop",
            {"type": "exercise", "name": "cardio", "detail": long_text},
            {"type": "exercise", "name": "cardio", "detail": long_text},
            {"type": "meal", "name": "breakfast", "detail": "oats"},
            {"type": "meal", "name": "breakfast", "detail": "oats"},
        ],
        recent_recommendations={
            "workout": {"cardio": [f"run-{index}" for index in range(12)]},
            "diet": {"breakfast": ["oats", "oats", long_text]},
            "junk": {"x": long_text},
        },
    )
    parsed_today_exercise = json.loads(prompt.split("[TODAY_EXERCISE_EXCLUDE]\n", 1)[1].split("\n\n", 1)[0])
    parsed_today_diet = json.loads(prompt.split("[TODAY_DIET_EXCLUDE]\n", 1)[1].split("\n\n", 1)[0])
    recent_workout = json.loads(prompt.split("[RECENT_WORKOUT_RECOMMENDATIONS]\n", 1)[1].split("\n\n", 1)[0])
    recent_diet = json.loads(prompt.split("[RECENT_DIET_RECOMMENDATIONS]\n", 1)[1])
    assert_true(len(parsed_today_exercise) == 1 and len(parsed_today_exercise[0]) == 120, "home prompt should bound and dedupe exercise exclusions")
    assert_true(parsed_today_diet == ["oats"], "home prompt should dedupe diet exclusions")
    assert_true(len(recent_workout["cardio"]) == 5, "home prompt should cap recent workout history per slot")
    assert_true(len(recent_diet["breakfast"]) == 2 and len(recent_diet["breakfast"][1]) == 80, "home prompt should cap and truncate recent diet history")


def test_intent_routing_uses_canonical_aliases() -> None:
    assert_true(normalize_intent("create") == INTENT_PLAN, "create alias should normalize to plan intent")
    assert_true(normalize_intent("update-plan") == INTENT_MODIFY, "hyphenated modify alias should normalize")
    assert_true(route_intent({"intent": "create"}) == "retrieval_decision", "route should accept normalized aliases")


def test_strict_weak_rag_fails_closed() -> None:
    state = {
        "user_id": "strict-rag",
        "user_message": "diet plan for kidney disease",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "diet",
        "user_profile": {"medical_conditions": ["kidney disease"]},
        "profile_constraints": {
            "retrieval_constraints": ["kidney_disease"],
            "retrieval_critical_constraints": ["kidney_disease"],
        },
        "search_targets": ["vdb_external"],
    }
    spec = _build_retrieval_spec(state, state["user_message"], ["vdb_external"])
    assert_true(_weak_external_should_fail_closed(state, spec, "weak"), "strict weak RAG should fail closed")

    low_risk_state = {
        **state,
        "user_profile": {"goal": "fitness"},
        "profile_constraints": {"retrieval_constraints": ["low_time"], "retrieval_critical_constraints": []},
    }
    low_risk_spec = _build_retrieval_spec(low_risk_state, "quick workout", ["vdb_external"])
    assert_true(
        not _weak_external_should_fail_closed(low_risk_state, low_risk_spec, "weak"),
        "low-risk weak RAG should not fail closed",
    )


def test_persona_style_report_flags_plan_shape() -> None:
    long_plan = "\n".join(f"- item {index}" for index in range(22))
    report = _persona_style_report(long_plan, {"intent": INTENT_PLAN}, "cheer_sis")
    codes = {violation["code"] for violation in report["violations"]}
    assert_true("persona_plan_response_too_many_lines" in codes, "persona style report should flag long plan shape")


def test_persona_plan_renderer_uses_distinct_core_and_approval() -> None:
    state = {
        "intent": INTENT_PLAN,
        "domain": "workout",
        "proposed_plan_type": "workout",
        "proposed_plan_action": "create",
        "user_message": "일주일치 운동 플랜 작성해줘",
    }
    components = normalize_draft_components(
        {
            "core_message": "운동 플랜을 제안해요.",
            "plan_preview": "- 2026-06-04 가벼운 전신 루틴: 의자 스쿼트 2세트",
            "approval_question": "이 운동 플랜으로 작성할까요?",
        }
    )
    draft = render_draft_preview(components)
    personas = [
        "cheer_sis",
        "soft_senior",
        "strict_trainer",
        "science_coach",
        "playful_buddy",
        "daily_manager",
    ]
    expected_markers = {
        "cheer_sis": ("밝게", "충분해요", "잘 맞춰볼게요"),
        "soft_senior": ("무리 없게", "천천히", "괜찮을까요"),
        "strict_trainer": ("바로", "간다", "작성할까"),
        "science_coach": ("기준", "안전성", "지속 가능성"),
        "playful_buddy": ("같이", "가보자", "부담 낮게"),
        "daily_manager": ("캘린더", "정리했습니다", "작성할까요"),
    }

    first_lines: dict[str, str] = {}
    approval_lines: dict[str, str] = {}
    for persona_id in personas:
        rendered = normalize_plan_flow_preview(draft, state, components, persona_id)
        lines = [line.strip() for line in rendered.splitlines() if line.strip()]
        first_lines[persona_id] = lines[0]
        approval_lines[persona_id] = lines[-1]
        assert_true(lines[1] == components["plan_preview"], "persona renderer should not mutate plan preview")
        for marker in expected_markers[persona_id]:
            assert_true(marker in rendered, f"{persona_id} should expose marker {marker}")

    assert_true(len(set(first_lines.values())) == len(personas), "persona core lines should be distinct")
    assert_true(len(set(approval_lines.values())) == len(personas), "persona approval questions should be distinct")


def test_persona_signature_adds_distinct_non_plan_tail() -> None:
    base_text = "걷기는 낮은 강도로 시작하기 좋습니다."
    personas = [
        "cheer_sis",
        "soft_senior",
        "strict_trainer",
        "science_coach",
        "playful_buddy",
        "daily_manager",
    ]
    outputs = {
        persona_id: apply_persona_signature(base_text, persona_id, {"intent": INTENT_INFO})
        for persona_id in personas
    }
    expected_markers = {
        "cheer_sis": "좋아요",
        "soft_senior": "천천히",
        "strict_trainer": "핵심",
        "science_coach": "선택 기준",
        "playful_buddy": "같이",
        "daily_manager": "캘린더 기준",
    }

    assert_true(len(set(outputs.values())) == len(personas), "non-plan persona tails should be distinct")
    for persona_id, output in outputs.items():
        assert_true(output.startswith(base_text), "persona signature should keep result-first answer")
        assert_true(expected_markers[persona_id] in output, f"{persona_id} should expose a distinct tail")


def test_persona_signature_styles_plan_flow_without_preview() -> None:
    base_text = "운동/식단 구분이 섞여서 이 플랜은 확정하지 않을게요."
    personas = [
        "cheer_sis",
        "soft_senior",
        "strict_trainer",
        "science_coach",
        "playful_buddy",
        "daily_manager",
    ]
    outputs = {
        persona_id: apply_persona_signature(base_text, persona_id, {"intent": INTENT_MODIFY})
        for persona_id in personas
    }
    expected_markers = {
        "cheer_sis": "맞춰볼게요",
        "soft_senior": "무리 없게",
        "strict_trainer": "핵심",
        "science_coach": "기준",
        "playful_buddy": "같이",
        "daily_manager": "캘린더 반영 기준",
    }

    assert_true(len(set(outputs.values())) == len(personas), "plan-flow tails should be distinct without preview")
    for persona_id, output in outputs.items():
        assert_true(output.startswith(base_text), "plan-flow persona tail should keep result-first answer")
        assert_true(expected_markers[persona_id] in output, f"{persona_id} should style no-preview plan flow")


def test_pinecone_profile_suite_marks_external_unavailable_as_blocked() -> None:
    from scripts.test_pinecone_profile_rag_v2_suite import (
        _build_report,
        _is_external_retrieval_dependency_unavailable,
        _render_markdown,
    )

    class FakeUnavailableError(Exception):
        code = 503

        def __str__(self) -> str:
            return "503 UNAVAILABLE. The service is currently unavailable."

    assert_true(
        _is_external_retrieval_dependency_unavailable(FakeUnavailableError()),
        "Pinecone profile RAG suite should classify external 503 as blocked, not code failure",
    )
    report = _build_report(
        [{"case_id": "trigger", "grade": "pass", "issues": []}],
        [],
        retrieval_status="blocked",
        blocked_details={"reason": "external_retrieval_dependency_unavailable"},
    )
    assert_true(report["summary"]["blocked"] is True, "blocked report should expose blocked=true")
    assert_true(report["summary"]["fail_count"] == 0, "external blocked report should not create a false test failure")

    missing_report = _build_report(
        [{"case_id": "trigger", "grade": "pass", "issues": []}],
        [],
        retrieval_status="blocked",
        blocked_details={
            "reason": "missing_external_credentials",
            "missing": ["GEMINI_API_KEY/ROUTER_API_KEY", "PINECONE_API_KEY"],
        },
    )
    rendered = _render_markdown(missing_report)
    assert_true("Missing credentials" in rendered, "blocked report markdown should expose missing credential names")
    assert_true(missing_report["summary"]["fail_count"] == 0, "missing credentials should not create a false code failure")


def main() -> None:
    tests = [
        test_stretching_beats_cardio_label,
        test_concrete_strength_exercises_override_stretching_label,
        test_was_payload_aligns_past_week_plan_dates_to_today,
        test_was_weekday_inference_never_schedules_past_day,
        test_profile_sensitive_workout_adjustment,
        test_diet_allergy_concrete_replacement,
        test_diet_profile_concrete_adaptation,
        test_plan_request_separation_and_question_copy,
        test_retrieval_goal_aliases_feed_external_filter,
        test_retrieval_spec_sanitizes_malformed_profile_constraints,
        test_malformed_confidence_intensity_and_scores_are_safe,
        test_retrieval_decision_sanitizes_targets_and_context,
        test_home_recommendation_display_bounds,
        test_home_recommendation_replaces_profile_conflicts,
        test_home_recommendation_guard_flags_medical_synonyms,
        test_home_recommendation_routes_through_validator,
        test_home_recommendation_validator_blocks_profile_conflicts,
        test_home_recommendation_blocks_advanced_risk_taxonomy,
        test_home_generation_quality_flags_capture_raw_repairs,
        test_iso_date_rejects_invalid_calendar_dates,
        test_profile_refresh_change_invalidates_active_proposal_context,
        test_home_profile_aliases_feed_prompt_and_allergy_guard,
        test_profile_override_clears_canonical_alias_siblings,
        test_profile_change_clears_pending_sequential_context,
        test_was_profile_merge_clears_alias_siblings_and_plan_context,
        test_profile_constraints_merge_clears_aliases_and_metadata,
        test_profile_constraints_node_clears_pending_sequential_plan,
        test_profile_constraints_tolerates_malformed_profile_state,
        test_explicit_both_plan_request_clarifies,
        test_simple_condition_statement_routes_casual,
        test_mixed_plan_clarification_followup_starts_workout_first,
        test_pending_sequential_followup_routes_next_domain,
        test_pending_sequential_checkpoint_policy,
        test_resumed_checkpoint_sanitizes_proposal_and_pending_state,
        test_recent_dialogue_hydration_sanitizes_checkpoint_memory,
        test_home_profile_issue_domains_are_section_specific,
        test_mixed_active_proposal_is_sanitized,
        test_conversation_state_helpers_sanitize_malformed_inputs,
        test_semantic_failure_recovers_with_safe_plan_fallback,
        test_validator_handles_malformed_state_containers,
        test_langsmith_quality_tracks_fallback_recovery,
        test_langsmith_quality_tracks_evidence_and_profile_coverage,
        test_langsmith_quality_handles_malformed_numeric_signals,
        test_langsmith_quality_tracks_goal_fit_warnings,
        test_plan_output_omits_constraint_exposition,
        test_diet_payload_stores_food_only,
        test_week_workout_plan_expands_from_one_day_request,
        test_seven_day_diet_plan_expands_by_calendar_day,
        test_weekly_diet_plan_expands_for_real_korean_chi_request,
        test_weekly_diet_preview_groups_by_calendar_day,
        test_weekly_workout_plan_expands_for_real_korean_chi_request,
        test_week_plan_without_start_date_aligns_to_today,
        test_weekly_workout_preview_shows_all_seven_days,
        test_modify_fallback_reuses_active_proposal,
        test_month_diet_plan_expands_by_calendar_day,
        test_month_workout_plan_expands_weekly_sessions,
        test_demo_plan_rag_degraded_does_not_fail_closed,
        test_high_risk_plan_rag_degraded_fails_closed,
        test_validator_blocks_high_risk_diet_conflicts,
        test_invalid_llm_plan_contract_triggers_fallback,
        test_modify_without_active_plan_creates_new_proposal,
        test_demo_plan_semantic_judge_blocks_profile_sensitive_plans,
        test_explicit_workout_overrides_wrong_draft_plan_type,
        test_safe_diet_fallback_respects_compound_allergies,
        test_pending_writes_are_bounded_and_dead_lettered,
        test_chat_router_summarizes_malformed_state_safely,
        test_today_plan_normalization_bounds_malformed_was_payload,
        test_record_and_preprocess_tolerate_malformed_write_state,
        test_outbox_reconcile_clears_succeeded_checkpoint_writes,
        test_finalize_mojibake_guard_can_repair_plan_response,
        test_finalize_handles_malformed_response_containers,
        test_dairy_free_replacement_is_not_allergen_conflict,
        test_diet_constraint_conflict_triggers_safe_fallback,
        test_goal_fit_warnings_are_profile_sensitive,
        test_validator_blocks_medical_diet_synonyms,
        test_explicit_new_domain_ignores_active_proposal_context,
        test_context_resolver_sanitizes_malformed_reference_state,
        test_home_recommendation_prompt_covers_profile_edges,
        test_home_prompt_bounds_plan_and_recent_inputs,
        test_intent_routing_uses_canonical_aliases,
        test_strict_weak_rag_fails_closed,
        test_persona_style_report_flags_plan_shape,
        test_persona_plan_renderer_uses_distinct_core_and_approval,
        test_persona_signature_adds_distinct_non_plan_tail,
        test_persona_signature_styles_plan_flow_without_preview,
        test_pinecone_profile_suite_marks_external_unavailable_as_blocked,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"TOTAL {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
