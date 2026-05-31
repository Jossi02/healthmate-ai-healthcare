"""Pinecone-only profile RAG trigger, filter, and evidence-fit suite.

This does not test LangGraph answer generation. It verifies that the current
frontend/backend profile fields drive retrieval decisions and Pinecone filters
correctly.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pinecone import PineconeAsyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.clients.embedding import EmbeddingClient
from app.clients.pinecone import PineconeClient
from app.graph.nodes.search import (
    EXTERNAL_FETCH_TOP_K,
    _apply_rag_trigger_targets,
    _build_external_filters,
    _build_retrieval_spec,
    _normalize_targets,
    _post_filter_external_results,
    _rerank_external_results,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_JSON_PATH = ROOT / "docs" / "quality" / "pinecone_profile_rag_v2_report.json"
REPORT_MD_PATH = ROOT / "docs" / "quality" / "pinecone_profile_rag_v2_report.md"
SEARCH_TOP_K = EXTERNAL_FETCH_TOP_K

INTENT_PLAN = "계획"
INTENT_MODIFY = "수정"
INTENT_INFO = "정보"
INTENT_RECORD = "기록"
INTENT_APPROVAL = "계획_승인"
INTENT_CASUAL = "casual"
INTENT_SAFETY = "안전경고"


BASE_PROFILE = {
    "age": 32,
    "gender": "female",
    "height": 165,
    "weight": 60,
    "goal": "건강 유지",
    "activity_level": "보통",
    "mbti": "INFP",
    "medical_history": ["없음"],
    "conditions": ["없음"],
    "injury_history": [],
    "allergies": ["해당 없음"],
    "diet_type": None,
    "selected_ai_persona": "cheer_sis",
}


TRIGGER_CASES = [
    {
        "case_id": "low_risk_light_plan_skips_rag",
        "message": "오늘 10분 가볍게 운동 플랜 작성해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "workout",
        "profile": BASE_PROFILE,
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": [],
    },
    {
        "case_id": "none_values_do_not_trigger_risk",
        "message": "오늘 식단 플랜 작성해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "diet",
        "profile": BASE_PROFILE,
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": [],
    },
    {
        "case_id": "evidence_question_uses_external",
        "message": "왜 이렇게 구성했는지 근거 알려줘",
        "intent": INTENT_INFO,
        "action_intent": "info",
        "domain": "workout",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": ["vdb_external"],
    },
    {
        "case_id": "research_without_recency_uses_external_only",
        "message": "운동 강도 논문 근거 알려줘",
        "intent": INTENT_INFO,
        "action_intent": "info",
        "domain": "workout",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": ["vdb_external"],
    },
    {
        "case_id": "latest_guideline_allows_web",
        "message": "최신 가이드라인 기준으로 유산소 운동 근거 알려줘",
        "intent": INTENT_INFO,
        "action_intent": "info",
        "domain": "workout",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": ["vdb_external", "web"],
    },
    {
        "case_id": "memory_reference_uses_user_memory_only",
        "message": "전에 내가 싫어한다고 한 음식 빼고 식단 수정해줘",
        "intent": INTENT_MODIFY,
        "action_intent": "modify",
        "domain": "diet",
        "profile": BASE_PROFILE,
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": ["vdb_memory", "vdb_user_important"],
    },
    {
        "case_id": "memory_and_external_modify_both",
        "message": "전에 싫어한다고 한 음식 빼고 당뇨 식단으로 수정해줘",
        "intent": INTENT_MODIFY,
        "action_intent": "modify",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "medical_history": ["당뇨"], "conditions": ["당뇨"]},
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": ["vdb_external", "vdb_memory", "vdb_user_important"],
    },
    {
        "case_id": "approval_skips_rag",
        "message": "좋아 이대로 해줘",
        "intent": INTENT_APPROVAL,
        "action_intent": "approval",
        "domain": "workout",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": [],
    },
    {
        "case_id": "record_skips_rag",
        "message": "운동 완료 체크해줘",
        "intent": INTENT_RECORD,
        "action_intent": "record",
        "domain": "workout",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": [],
    },
    {
        "case_id": "profile_write_skips_rag",
        "message": "나 유제품 알레르기 있어",
        "intent": INTENT_RECORD,
        "action_intent": "record",
        "domain": "profile",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": [],
    },
    {
        "case_id": "safety_route_skips_search_node",
        "message": "가슴이 답답하고 숨이 차",
        "intent": INTENT_SAFETY,
        "action_intent": "safety",
        "domain": "general",
        "profile": BASE_PROFILE,
        "initial_targets": [],
        "expect_targets": [],
    },
    {
        "case_id": "mbti_style_only_skips_external",
        "message": "ENFP 성향에 맞게 운동 추천해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "mbti": "ENFP"},
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": [],
    },
    {
        "case_id": "mbti_with_condition_uses_external",
        "message": "ENFP 성향인데 고혈압 있어 운동 플랜 작성해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "mbti": "ENFP", "medical_history": ["고혈압"], "conditions": ["고혈압"]},
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": ["vdb_external"],
    },
    {
        "case_id": "mixed_none_and_real_allergy_triggers_external",
        "message": "견과류 알레르기 피해서 식단 작성해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["해당 없음", "견과류"]},
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": ["vdb_external"],
    },
    {
        "case_id": "bmi_high_plan_triggers_external",
        "message": "다이어트 운동 플랜 작성해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "height": 150, "weight": 72, "bmi": 32.0, "goal": "다이어트"},
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": ["vdb_external"],
    },
    {
        "case_id": "front_condition_hypertension_triggers_external",
        "message": "고혈압이 있는데 운동 플랜 작성해줘",
        "intent": INTENT_PLAN,
        "action_intent": "create",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "medical_history": ["고혈압"], "conditions": ["고혈압"]},
        "initial_targets": ["vdb_external", "vdb_memory", "vdb_user_important", "web"],
        "expect_targets": ["vdb_external"],
    },
]


RETRIEVAL_CASES = [
    {
        "case_id": "front_condition_hypertension_workout",
        "message": "고혈압이 있는데 안전한 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "medical_history": ["고혈압"], "conditions": ["고혈압"]},
        "expect_targets": ["general_adult"],
        "expect_constraints": ["hypertension"],
        "expect_goals": ["heart_health"],
        "expect_domain": "workout",
    },
    {
        "case_id": "front_condition_diabetes_diet",
        "message": "혈당 관리 식단 플랜 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "goal": "건강 유지", "medical_history": ["당뇨"], "conditions": ["당뇨"]},
        "expect_constraints": ["diabetes"],
        "expect_goals": ["glucose_control"],
        "expect_domain": "diet",
    },
    {
        "case_id": "combined_hypertension_diabetes_diet",
        "message": "고혈압이랑 당뇨가 있는데 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "medical_history": ["고혈압", "당뇨"], "conditions": ["고혈압", "당뇨"]},
        "expect_constraints": ["hypertension", "diabetes"],
        "expect_goals": ["heart_health", "glucose_control"],
        "expect_domain": "diet",
        "expect_topics": ["diabetes_nutrition", "heart_health_nutrition"],
        "expect_kb_ids": ["diet_hypertension_diabetes_combined"],
        "min_strict_count": 2,
    },
    {
        "case_id": "general_domain_diet_inferred_hypertension",
        "message": "고혈압 있는데 식단 부탁해",
        "domain": "general",
        "profile": {**BASE_PROFILE, "medical_history": ["고혈압"], "conditions": ["고혈압"]},
        "expect_constraints": ["hypertension"],
        "expect_goals": ["heart_health"],
        "expect_domain": "diet",
    },
    {
        "case_id": "negative_hypertension_positive_diabetes_diet",
        "message": "고혈압은 없고 당뇨만 있어 식단 작성해줘",
        "domain": "general",
        "profile": BASE_PROFILE,
        "expect_constraints": ["diabetes"],
        "reject_filter_constraints": ["hypertension"],
        "expect_negative_constraints": ["hypertension"],
        "expect_goals": ["glucose_control"],
        "expect_domain": "diet",
    },
    {
        "case_id": "front_condition_arthritis_workout",
        "message": "관절염이 있는데 무리 없는 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "medical_history": ["관절염"], "conditions": ["관절염"]},
        "expect_constraints": ["arthritis"],
        "expect_domain": "workout",
    },
    {
        "case_id": "older_adult_arthritis_workout",
        "message": "관절염 있고 66세인데 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "age": 66, "activity_level": "가벼운 활동", "medical_history": ["관절염"], "conditions": ["관절염"]},
        "expect_targets": ["older_adult", "beginner"],
        "expect_constraints": ["arthritis"],
        "expect_goals": ["mobility"],
        "expect_domain": "workout",
        "expect_topics": ["pain_adaptation"],
        "expect_kb_ids": ["workout_older_adult_arthritis"],
        "min_strict_count": 2,
    },
    {
        "case_id": "front_condition_asthma_workout",
        "message": "천식이 있는데 숨차지 않게 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "medical_history": ["천식"], "conditions": ["천식"]},
        "expect_constraints": ["asthma"],
        "expect_domain": "workout",
    },
    {
        "case_id": "front_condition_cardiovascular_workout",
        "message": "심혈관 질환이 있는데 안전한 유산소 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "medical_history": ["심혈관 질환"], "conditions": ["심혈관 질환"]},
        "expect_constraints": ["cardiovascular_disease"],
        "expect_goals": ["heart_health"],
        "expect_domain": "workout",
    },
    {
        "case_id": "front_allergy_dairy_diet",
        "message": "유제품 알레르기 피해서 단백질 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["유제품"]},
        "expect_targets": ["food_allergy"],
        "expect_constraints": ["food_allergy", "dairy_allergy"],
        "expect_domain": "diet",
    },
    {
        "case_id": "front_allergy_nut_diet",
        "message": "견과류 알레르기 피해서 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["견과류"]},
        "expect_constraints": ["food_allergy", "nut_allergy"],
        "expect_domain": "diet",
    },
    {
        "case_id": "mixed_none_and_nut_allergy_diet",
        "message": "유제품은 해당 없음이고 견과류 알레르기만 있어 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["해당 없음", "견과류"]},
        "expect_targets": ["food_allergy"],
        "expect_constraints": ["food_allergy", "nut_allergy"],
        "reject_filter_constraints": ["dairy_allergy"],
        "expect_domain": "diet",
        "expect_topics": ["food_allergy"],
        "expect_kb_ids": ["diet_mixed_allergy_none_guard"],
    },
    {
        "case_id": "front_allergy_shellfish_diet",
        "message": "갑각류 알레르기 피해서 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["갑각류"]},
        "expect_constraints": ["food_allergy", "shellfish_allergy"],
        "expect_domain": "diet",
    },
    {
        "case_id": "front_allergy_wheat_diet",
        "message": "밀 알레르기 피해서 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["밀"]},
        "expect_constraints": ["food_allergy", "wheat_allergy"],
        "expect_domain": "diet",
    },
    {
        "case_id": "front_allergy_soy_diet",
        "message": "대두 알레르기 피해서 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["대두"]},
        "expect_constraints": ["food_allergy", "soy_allergy"],
        "expect_domain": "diet",
    },
    {
        "case_id": "front_allergy_egg_diet",
        "message": "달걀 알레르기 피해서 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "allergies": ["달걀"]},
        "expect_constraints": ["food_allergy", "egg_allergy"],
        "expect_domain": "diet",
    },
    {
        "case_id": "older_adult_profile_workout",
        "message": "안전한 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "age": 68, "activity_level": "거의 없음"},
        "expect_targets": ["older_adult", "beginner"],
        "expect_domain": "workout",
    },
    {
        "case_id": "minor_profile_fat_loss_diet",
        "message": "다이어트 식단 플랜 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "age": 17, "goal": "다이어트"},
        "expect_targets": ["minor"],
        "expect_goals": ["fat_loss"],
        "expect_domain": "diet",
    },
    {
        "case_id": "high_weight_knee_workout",
        "message": "무릎 부담 적은 다이어트 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "weight": 96, "goal": "다이어트", "injury_history": ["무릎 통증"]},
        "expect_targets": ["high_weight"],
        "expect_constraints": ["knee_pain"],
        "expect_goals": ["fat_loss"],
        "expect_domain": "workout",
    },
    {
        "case_id": "negative_knee_pain_workout",
        "message": "무릎 통증은 없어 다이어트 운동 플랜 작성해줘",
        "domain": "general",
        "profile": {**BASE_PROFILE, "goal": "다이어트"},
        "reject_filter_constraints": ["knee_pain"],
        "expect_negative_constraints": ["knee_pain"],
        "expect_goals": ["fat_loss"],
        "expect_domain": "workout",
    },
    {
        "case_id": "bmi_high_low_impact_workout",
        "message": "키 150cm, 몸무게 72kg인데 다이어트 운동 플랜 작성해줘",
        "domain": "workout",
        "profile": {**BASE_PROFILE, "height": 150, "weight": 72, "bmi": 32.0, "goal": "다이어트", "activity_level": "가벼운 활동"},
        "expect_targets": ["high_weight", "beginner"],
        "expect_constraints": ["obesity"],
        "expect_goals": ["fat_loss"],
        "expect_domain": "workout",
        "expect_topics": ["physical_activity"],
        "expect_kb_ids": ["workout_bmi_high_low_impact"],
    },
    {
        "case_id": "plant_based_muscle_gain_diet",
        "message": "채식 기준으로 근력 향상 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "goal": "근력 향상", "diet_type": "vegetarian"},
        "expect_targets": ["plant_based"],
        "expect_constraints": ["vegetarian"],
        "expect_goals": ["muscle_gain"],
        "expect_domain": "diet",
    },
    {
        "case_id": "plant_based_dairy_allergy_muscle_gain_diet",
        "message": "채식 중이고 유제품 알레르기도 있는데 근력 향상 식단 작성해줘",
        "domain": "diet",
        "profile": {**BASE_PROFILE, "goal": "근력 향상", "diet_type": "vegetarian", "allergies": ["유제품"]},
        "expect_targets": ["plant_based", "food_allergy"],
        "expect_constraints": ["vegetarian", "dairy_allergy", "food_allergy"],
        "expect_goals": ["muscle_gain"],
        "expect_domain": "diet",
        "expect_topics": ["protein", "food_allergy"],
        "expect_kb_ids": ["diet_plant_based_dairy_allergy_muscle_gain"],
    },
    {
        "case_id": "stretching_mobility_plan",
        "message": "스트레칭 위주로 일주일 운동 플랜 작성해줘",
        "domain": "general",
        "profile": BASE_PROFILE,
        "expect_goals": ["mobility"],
        "expect_domain": "workout",
        "expect_topics": ["mobility"],
        "expect_kb_ids": ["workout_mobility_stretching_classification"],
    },
    {
        "case_id": "low_activity_evidence_filter_beginner",
        "message": "유산소 심박수 근거 알려줘",
        "domain": "workout",
        "intent": INTENT_INFO,
        "action_intent": "info",
        "profile": {**BASE_PROFILE, "activity_level": "거의 없음"},
        "expect_targets": ["beginner"],
        "expect_domain": "workout",
    },
]


def _state(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": "pinecone-profile-rag-test",
        "user_message": case["message"],
        "intent": case.get("intent", INTENT_PLAN),
        "action_intent": case.get("action_intent", "create"),
        "domain": case.get("domain", "general"),
        "user_profile": case.get("profile") or {},
        "search_targets": list(case.get("initial_targets") or []),
        "context_resolution": {
            "resolved_reference": "none",
            "resolved_domain": case.get("domain", "general"),
            "resolved_text": "",
            "confidence": 0.0,
            "ambiguous": False,
        },
    }


def _decide(case: dict[str, Any]) -> dict[str, Any]:
    state = _state(case)
    query = str(state["user_message"])
    spec = _build_retrieval_spec(state, query, list(case.get("initial_targets") or []))
    return {
        "query": spec.query_span,
        "targets": spec.targets,
        "strict_filter": spec.strict_filter,
        "relaxed_filter": spec.relaxed_filter,
        "spec": spec,
    }


def _flatten_filter_values(metadata_filter: Any, key: str) -> list[str]:
    values: list[str] = []
    if isinstance(metadata_filter, dict):
        for item_key, value in metadata_filter.items():
            if item_key == key and isinstance(value, dict):
                raw = value.get("$in")
                if isinstance(raw, list):
                    values.extend(str(item) for item in raw)
            elif isinstance(value, list):
                for item in value:
                    values.extend(_flatten_filter_values(item, key))
            elif isinstance(value, dict):
                values.extend(_flatten_filter_values(value, key))
    return list(dict.fromkeys(values))


def evaluate_trigger_cases() -> list[dict[str, Any]]:
    results = []
    for case in TRIGGER_CASES:
        decision = _decide(case)
        expected = sorted(case.get("expect_targets") or [])
        actual = sorted(decision["targets"])
        issues = []
        if actual != expected:
            issues.append(f"targets expected={expected} actual={actual}")
        results.append(
            {
                "case_id": case["case_id"],
                "grade": "pass" if not issues else "fail",
                "issues": issues,
                "expected_targets": expected,
                "actual_targets": actual,
                "query": decision["query"],
            }
        )
    return results


async def evaluate_retrieval_cases(client: PineconeClient, embed: EmbeddingClient) -> list[dict[str, Any]]:
    results = []
    for case in RETRIEVAL_CASES:
        decision = _decide(case)
        issues = []
        strict_filter = decision["strict_filter"]
        expected_domain = case.get("expect_domain")
        expected_targets = case.get("expect_targets") or []
        expected_constraints = case.get("expect_constraints") or []
        expected_goals = case.get("expect_goals") or []
        expected_topics = case.get("expect_topics") or []
        expected_kb_ids = set(case.get("expect_kb_ids") or [])
        rejected_filter_constraints = case.get("reject_filter_constraints") or []
        expected_negative_constraints = case.get("expect_negative_constraints") or []
        expected_critical_constraints = case.get("expect_critical_constraints") or expected_constraints
        min_strict_count = int(case.get("min_strict_count") or 0)
        spec = decision["spec"]

        if "vdb_external" not in decision["targets"] and case.get("intent", INTENT_PLAN) != INTENT_INFO:
            issues.append("external target missing for retrieval case")

        domain_values = _flatten_filter_values(strict_filter, "domain")
        target_values = _flatten_filter_values(strict_filter, "profile_targets")
        constraint_values = _flatten_filter_values(strict_filter, "constraints")
        goal_values = _flatten_filter_values(strict_filter, "goals")

        if expected_domain and expected_domain not in domain_values:
            issues.append(f"domain filter missing {expected_domain}")
        for value in expected_targets:
            if value not in target_values:
                issues.append(f"profile target filter missing {value}")
        for value in expected_constraints:
            if value not in constraint_values:
                issues.append(f"constraint filter missing {value}")
        for value in rejected_filter_constraints:
            if value in constraint_values:
                issues.append(f"constraint filter should not include {value}")
        for value in expected_negative_constraints:
            if value not in spec.negative_constraints:
                issues.append(f"negative constraint missing {value}")
        for value in expected_critical_constraints:
            if value not in spec.critical_constraints:
                issues.append(f"critical constraint missing {value}")
        for value in expected_goals:
            if value not in goal_values:
                issues.append(f"goal filter missing {value}")

        vector = await embed.embed(decision["query"])
        strict_results = await client.search_external(vector, top_k=SEARCH_TOP_K, metadata_filter=strict_filter)
        relaxed_results = []
        if len(strict_results) < 2 and decision["relaxed_filter"]:
            relaxed_results = await client.search_external(
                vector,
                top_k=SEARCH_TOP_K,
                metadata_filter=decision["relaxed_filter"],
            )
        semantic_results = []
        if len(strict_results) + len(relaxed_results) < 2:
            semantic_results = await client.search_external(vector, top_k=SEARCH_TOP_K)

        combined = _merge_by_id(strict_results + relaxed_results + semantic_results)
        combined, post_filter_quality = _post_filter_external_results(combined, spec)
        combined = _rerank_external_results(combined, spec)[:8]
        if min_strict_count and len(strict_results) < min_strict_count:
            issues.append(f"strict results expected>={min_strict_count} actual={len(strict_results)}")
        if expected_topics and not any(result.get("topic") in expected_topics for result in combined):
            issues.append(f"no evidence matched expected topics {expected_topics}")
        if expected_kb_ids:
            returned_kb_ids = {
                str(result.get("kb_id") or (result.get("metadata") or {}).get("kb_id") or "")
                for result in combined
            }
            if not expected_kb_ids & returned_kb_ids:
                issues.append(f"expected kb_id missing {sorted(expected_kb_ids)}")
        relevant_count = _count_relevant(combined, case)
        if len(combined) < 1:
            issues.append("no Pinecone evidence returned")
        if relevant_count < 1:
            issues.append("no evidence matched expected metadata")

        top = combined[0] if combined else {}
        results.append(
            {
                "case_id": case["case_id"],
                "grade": "pass" if not issues else "fail",
                "issues": issues,
                "query": decision["query"],
                "strict_filter": strict_filter,
                "relaxed_filter": decision["relaxed_filter"],
                "strict_count": len(strict_results),
                "relaxed_count": len(relaxed_results),
                "semantic_count": len(semantic_results),
                "post_filter_quality": post_filter_quality,
                "critical_constraints": spec.critical_constraints,
                "negative_constraints": spec.negative_constraints,
                "relevant_count": relevant_count,
                "top": {
                    "score": round(float(top.get("score") or 0.0), 4) if top else 0.0,
                    "kb_id": top.get("kb_id") or (top.get("metadata") or {}).get("kb_id"),
                    "chunk_title": top.get("chunk_title"),
                    "source_title": top.get("source_title"),
                    "domain": top.get("domain"),
                    "topic": top.get("topic"),
                    "profile_targets": top.get("profile_targets"),
                    "constraints": top.get("constraints"),
                    "goals": top.get("goals"),
                    "risk_level": top.get("risk_level"),
                },
            }
        )
    return results


def _merge_by_id(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for result in sorted(results, key=lambda item: float(item.get("score") or 0.0), reverse=True):
        key = str(result.get("id") or result.get("text") or "")
        if key and key not in seen:
            seen.add(key)
            merged.append(result)
    return merged[:SEARCH_TOP_K]


def _count_relevant(results: list[dict[str, Any]], case: dict[str, Any]) -> int:
    expected_domain = case.get("expect_domain")
    expected_targets = set(case.get("expect_targets") or [])
    expected_constraints = set(case.get("expect_constraints") or [])
    expected_goals = set(case.get("expect_goals") or [])
    expected_topics = set(case.get("expect_topics") or [])
    expected_kb_ids = set(case.get("expect_kb_ids") or [])
    count = 0
    for result in results:
        domain_ok = not expected_domain or result.get("domain") == expected_domain
        targets = set(result.get("profile_targets") or [])
        constraints = set(result.get("constraints") or [])
        goals = set(result.get("goals") or [])
        kb_id = str(result.get("kb_id") or (result.get("metadata") or {}).get("kb_id") or "")
        target_ok = not expected_targets or expected_targets.issubset(targets)
        constraint_ok = not expected_constraints or expected_constraints.issubset(constraints)
        goal_ok = not expected_goals or expected_goals.issubset(goals)
        topic_ok = not expected_topics or result.get("topic") in expected_topics
        kb_ok = not expected_kb_ids or kb_id in expected_kb_ids
        if domain_ok and target_ok and constraint_ok and goal_ok and topic_ok and kb_ok:
            count += 1
    return count


async def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("ROUTER_API_KEY")
    pinecone_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME", "health-coach-ai")
    if not gemini_key or not pinecone_key:
        raise RuntimeError("GEMINI_API_KEY/ROUTER_API_KEY and PINECONE_API_KEY are required")

    embed = EmbeddingClient(api_key=gemini_key)
    pc_core = PineconeAsyncio(api_key=pinecone_key)
    description = await pc_core.describe_index(index_name)
    index = pc_core.IndexAsyncio(host=description.host)
    client = PineconeClient(index=index)

    trigger_results = evaluate_trigger_cases()
    retrieval_results = await evaluate_retrieval_cases(client, embed)

    close_index = getattr(index, "close", None)
    if close_index:
        maybe_awaitable = close_index()
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable
    await pc_core.close()

    all_results = trigger_results + retrieval_results
    pass_count = sum(1 for result in all_results if result["grade"] == "pass")
    fail_count = len(all_results) - pass_count
    retrieval_relevant = [result["relevant_count"] for result in retrieval_results]
    report = {
        "summary": {
            "trigger_cases": len(trigger_results),
            "retrieval_cases": len(retrieval_results),
            "total_cases": len(all_results),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "accuracy": round(pass_count / len(all_results), 4) if all_results else 1.0,
            "avg_relevant_evidence": round(statistics.mean(retrieval_relevant), 3) if retrieval_relevant else 0.0,
        },
        "trigger_results": trigger_results,
        "retrieval_results": retrieval_results,
    }

    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD_PATH.write_text(_render_markdown(report), encoding="utf-8")

    print("[pinecone-profile-rag-v2] summary:", json.dumps(report["summary"], ensure_ascii=False))
    print("[pinecone-profile-rag-v2] report json:", REPORT_JSON_PATH)
    print("[pinecone-profile-rag-v2] report md:", REPORT_MD_PATH)

    if fail_count:
        raise SystemExit(1)


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Pinecone Profile RAG v2 Report",
        "",
        f"- Trigger cases: {summary['trigger_cases']}",
        f"- Retrieval cases: {summary['retrieval_cases']}",
        f"- Total cases: {summary['total_cases']}",
        f"- Accuracy: {summary['accuracy']}",
        f"- Pass/Fail: {summary['pass_count']}/{summary['fail_count']}",
        f"- Average relevant evidence: {summary['avg_relevant_evidence']}",
        "",
        "## Trigger Cases",
    ]
    for result in report["trigger_results"]:
        issues = ", ".join(result["issues"]) or "none"
        lines.append(
            f"- {result['case_id']}: {result['grade']} "
            f"expected={result['expected_targets']} actual={result['actual_targets']} issues={issues}"
        )

    lines.extend(["", "## Retrieval Cases"])
    for result in report["retrieval_results"]:
        issues = ", ".join(result["issues"]) or "none"
        top = result["top"]
        lines.append(
            f"- {result['case_id']}: {result['grade']} strict={result['strict_count']} "
            f"relaxed={result['relaxed_count']} semantic={result['semantic_count']} "
            f"relevant={result['relevant_count']} top={top.get('chunk_title')} "
            f"kb_id={top.get('kb_id')} topic={top.get('topic')} issues={issues}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    asyncio.run(main())
