from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.conversation_state import infer_domain
from app.core.draft_contract import normalize_draft_components, render_draft_preview
from app.core.was_outbox import (
    enqueue_was_outbox,
    mark_was_outbox_succeeded,
    reconcile_pending_writes_with_outbox,
)
from app.graph.nodes.generate import (
    _adjust_diet_plan_for_profile,
    _adjust_workout_plan_for_profile,
    _build_mixed_plan_clarification_draft,
    _build_modify_plan_fallback,
    _diet_plan_requires_safe_fallback,
    _expand_long_range_plan_if_requested,
    _is_mixed_plan_type_request,
    _minimize_plan_exposition,
    _normalize_plan_core_message,
    _normalize_plan_approval_question,
    _plan_contract_needs_fallback,
    _render_plan_preview_from_items,
    _resolve_proposed_plan_type,
    _response_render_state,
    _workout_item_category,
)
from app.graph.nodes.context_resolver import _resolve_context
from app.graph.nodes.answer_validator import (
    _requires_external_fail_closed,
    _safe_diet_fallback_for_validation_failure,
    _semantic_validation_mode,
    _should_run_semantic_validation,
    _validate_state,
    _validation_quality_dimensions,
)
from app.services.home_recommendations import kst_today_iso
from app.graph.nodes.finalize import _looks_like_mojibake, _safe_response_from_state
from app.graph.nodes.preprocess import (
    _mark_pending_write_failed,
    _normalize_pending_writes,
    _pending_write_exhausted,
)
from app.graph.nodes.search import _build_retrieval_spec
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
    assert_true(not _is_mixed_plan_type_request("운동 계획과 식단 계획을 같이 짜줘"), "explicit both-domain request should be allowed as separated plans")

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


def test_demo_plan_semantic_judge_does_not_block_structured_plans() -> None:
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
        _semantic_validation_mode(plan_state) == "observe",
        "structured plan semantic judge should be observe-only so deterministic validators own blocking decisions",
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


def test_outbox_reconcile_clears_succeeded_checkpoint_writes() -> None:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        db_path = tmp.name

    async def scenario() -> None:
        writes = [
            {"write_type": "profile", "payload": {"nickname": "done"}, "write_id": "write-done"},
            {"write_type": "profile", "payload": {"nickname": "pending"}, "write_id": "write-pending"},
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


def main() -> None:
    tests = [
        test_stretching_beats_cardio_label,
        test_profile_sensitive_workout_adjustment,
        test_diet_allergy_concrete_replacement,
        test_diet_profile_concrete_adaptation,
        test_plan_request_separation_and_question_copy,
        test_retrieval_goal_aliases_feed_external_filter,
        test_home_recommendation_display_bounds,
        test_home_recommendation_replaces_profile_conflicts,
        test_home_recommendation_guard_flags_medical_synonyms,
        test_plan_output_omits_constraint_exposition,
        test_diet_payload_stores_food_only,
        test_week_workout_plan_expands_from_one_day_request,
        test_seven_day_diet_plan_expands_by_calendar_day,
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
        test_demo_plan_semantic_judge_does_not_block_structured_plans,
        test_explicit_workout_overrides_wrong_draft_plan_type,
        test_safe_diet_fallback_respects_compound_allergies,
        test_pending_writes_are_bounded_and_dead_lettered,
        test_outbox_reconcile_clears_succeeded_checkpoint_writes,
        test_finalize_mojibake_guard_can_repair_plan_response,
        test_dairy_free_replacement_is_not_allergen_conflict,
        test_diet_constraint_conflict_triggers_safe_fallback,
        test_validator_blocks_medical_diet_synonyms,
        test_explicit_new_domain_ignores_active_proposal_context,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"TOTAL {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
