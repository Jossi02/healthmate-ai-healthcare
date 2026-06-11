"""Fast LangGraph plan-flow quality suite.

Scope:
- 10 user profiles
- 10 labeled turns per profile
- request -> follow-up -> change -> follow-up -> approval -> WAS reflection
- workout/diet/date/slot/category/profile-conflict checks

This suite intentionally runs in-process with fake WAS storage so it can be
executed without Gemini, Pinecone, Supabase, or a running server.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from app.core.conversation_state import (  # noqa: E402
    empty_context_resolution,
    empty_recent_dialogue,
    evolve_active_proposal,
    sync_proposal_fields,
)
from app.core.intents import INTENT_APPROVAL, INTENT_RECORD  # noqa: E402
from app.graph.nodes.fast_flow import (  # noqa: E402
    make_fast_finalize_node,
    make_fast_generate_node,
    make_fast_profile_constraints_node,
    make_fast_router_node,
    make_fast_target_resource_node,
    make_fast_validate_node,
)
from app.graph.nodes.was_write import execute_was_writes  # noqa: E402


EXPECTED_LABELS: dict[str, dict[str, Any]] = {
    "diet_week_create": {
        "operation": "plan.create",
        "domain": "diet",
        "items": 21,
        "meal_slots_per_day": 3,
        "write": "proposal_only",
        "profile_fit": "no_forbidden_food",
    },
    "diet_breakfast_modify": {
        "operation": "plan.modify",
        "domain": "diet",
        "items": 21,
        "target": "all proposal breakfast slots",
        "write": "proposal_only",
        "profile_fit": "no_forbidden_food",
    },
    "diet_followup_question": {
        "operation": "info",
        "domain": "diet",
        "state": "active_proposal_must_remain",
    },
    "diet_approval_write": {
        "operation": "plan.approve",
        "domain": "diet",
        "was_reflection": "diet_count_21",
    },
    "workout_week_create": {
        "operation": "plan.create",
        "domain": "workout",
        "items": 7,
        "profile_fit": "no_forbidden_workout",
        "taxonomy": "stretching_must_not_be_cardio",
    },
    "workout_knee_modify": {
        "operation": "plan.modify",
        "domain": "workout",
        "items": 7,
        "profile_fit": "no_forbidden_workout",
    },
    "workout_followup_question": {
        "operation": "info",
        "domain": "workout",
        "state": "active_proposal_must_remain",
    },
    "workout_approval_write": {
        "operation": "plan.approve",
        "domain": "workout",
        "was_reflection": "workout_count_7",
    },
    "bundle_week_create": {
        "operation": "plan.create",
        "domain": "bundle",
        "items": 28,
        "meal_slots_per_day": 3,
        "taxonomy": "workout_and_diet_separated",
    },
    "delete_all_calendar": {
        "operation": "plan.delete",
        "domain": "all",
        "was_reflection": "workout_and_diet_empty",
        "state": "active_proposal_cleared",
    },
}


PROFILES: list[dict[str, Any]] = [
    {
        "profile_id": "p01_cheer_dairy_knee",
        "selected_ai_persona": "cheer_sis",
        "age": 25,
        "gender": "female",
        "goal": "건강 유지",
        "activity_level": "초보",
        "allergies": ["유제품"],
        "injury_history": ["무릎 통증"],
    },
    {
        "profile_id": "p02_pt_hypertension_weight",
        "selected_ai_persona": "strict_trainer",
        "age": 34,
        "gender": "male",
        "goal": "체중 감량",
        "activity_level": "보통",
        "medical_history": ["고혈압"],
    },
    {
        "profile_id": "p03_buddy_vegetarian",
        "selected_ai_persona": "playful_buddy",
        "age": 28,
        "goal": "건강 유지",
        "activity_level": "초보",
        "diet_type": "채식",
    },
    {
        "profile_id": "p04_manager_diabetes",
        "selected_ai_persona": "daily_manager",
        "age": 45,
        "goal": "체중 관리",
        "activity_level": "낮음",
        "medical_history": ["당뇨"],
    },
    {
        "profile_id": "p05_younger_vegan_shoulder",
        "selected_ai_persona": "soft_senior",
        "age": 31,
        "goal": "건강 유지",
        "activity_level": "초보",
        "diet_type": "비건",
        "injury_history": ["어깨 통증"],
    },
    {
        "profile_id": "p06_routine_muscle",
        "selected_ai_persona": "science_coach",
        "age": 23,
        "goal": "근육 증가",
        "activity_level": "보통",
    },
    {
        "profile_id": "p07_cheer_back_obesity",
        "selected_ai_persona": "cheer_sis",
        "age": 39,
        "goal": "체중 감량",
        "activity_level": "낮음",
        "injury_history": ["허리 통증"],
        "medical_history": ["비만"],
    },
    {
        "profile_id": "p08_pt_asthma",
        "selected_ai_persona": "strict_trainer",
        "age": 20,
        "goal": "체력 향상",
        "activity_level": "보통",
        "medical_history": ["천식"],
    },
    {
        "profile_id": "p09_manager_nut_hypertension",
        "selected_ai_persona": "daily_manager",
        "age": 52,
        "goal": "건강 유지",
        "activity_level": "낮음",
        "allergies": ["견과류"],
        "medical_history": ["고혈압"],
    },
    {
        "profile_id": "p10_buddy_dairy_egg",
        "selected_ai_persona": "playful_buddy",
        "age": 27,
        "goal": "건강 유지",
        "activity_level": "초보",
        "allergies": ["유제품", "계란"],
    },
]


TURNS: list[dict[str, str]] = [
    {"label": "diet_week_create", "message": "일주일치 식단 짜줘"},
    {"label": "diet_breakfast_modify", "message": "아침은 현미죽으로 바꿔줘"},
    {"label": "diet_followup_question", "message": "점심 단백질은 충분해?"},
    {"label": "diet_approval_write", "message": "응 반영해줘"},
    {"label": "workout_week_create", "message": "일주일치 운동 짜줘"},
    {"label": "workout_knee_modify", "message": "무릎 부담 줄여서 수정해줘"},
    {"label": "workout_followup_question", "message": "이 강도로 괜찮아?"},
    {"label": "workout_approval_write", "message": "좋아 적용해줘"},
    {"label": "bundle_week_create", "message": "운동이랑 식단 둘 다 일주일치 짜줘"},
    {"label": "delete_all_calendar", "message": "모두 제거해줘 캘린더 내용"},
]


class FakeTrace:
    def record_current_event(self, **kwargs: Any) -> None:
        return None

    def record_current_alert(self, **kwargs: Any) -> None:
        return None


class FakeWAS:
    def __init__(self) -> None:
        self.workout_items: list[dict[str, Any]] = []
        self.diet_items: list[dict[str, Any]] = []

    async def get_diet_plan_full(self, user_id: str) -> dict[str, Any]:
        return {"plan_type": "diet", "items": deepcopy(self.diet_items)}

    async def get_workout_plan_full(self, user_id: str) -> dict[str, Any]:
        return {"plan_type": "workout", "items": deepcopy(self.workout_items)}

    async def get_today_plan(self, user_id: str) -> list[dict[str, Any]]:
        return []

    async def post_plan_create(self, user_id: str, plan: dict[str, Any]) -> None:
        await self._write_plan(plan, replace=False)

    async def put_plan_update(self, user_id: str, plan: dict[str, Any]) -> None:
        await self._write_plan(plan, replace=True)

    async def delete_plan(self, user_id: str, payload: dict[str, Any]) -> None:
        plan_type = payload.get("plan_type")
        scope = payload.get("target_scope")
        dates = set(payload.get("target_dates") or [])
        if plan_type in {"workout", "all"}:
            self.workout_items = [] if scope == "all" else [item for item in self.workout_items if item.get("day") not in dates]
        if plan_type in {"diet", "all"}:
            self.diet_items = [] if scope == "all" else [item for item in self.diet_items if item.get("day") not in dates]

    async def put_plan_check(self, user_id: str, item_id: str, *, idempotency_key: str | None = None) -> None:
        return None

    async def put_user_profile(self, user_id: str, changes: dict[str, Any]) -> None:
        return None

    async def _write_plan(self, plan: dict[str, Any], *, replace: bool) -> None:
        plan_type = plan.get("plan_type")
        items = deepcopy(plan.get("items") or [])
        dates = {item.get("day") for item in items}
        if plan_type == "workout":
            if replace:
                self.workout_items = [item for item in self.workout_items if item.get("day") not in dates]
            self.workout_items.extend(items)
        elif plan_type == "diet":
            if replace:
                self.diet_items = [item for item in self.diet_items if item.get("day") not in dates]
            self.diet_items.extend(items)


class FakeDeps:
    def __init__(self, was: FakeWAS) -> None:
        self.trace = FakeTrace()
        self.was = was


def initial_state(profile: dict[str, Any], message: str, active_proposal: dict[str, Any] | None, turn_count: int) -> dict[str, Any]:
    return {
        "user_id": profile["profile_id"],
        "user_message": message,
        "request_kind": "chat",
        "user_profile": deepcopy(profile),
        "effective_user_profile": None,
        "pending_profile_overlay": None,
        "profile_override_applied": False,
        "today_plan": [],
        "turn_count": turn_count,
        "is_session_start": False,
        "intent": "",
        "action_intent": None,
        "domain": "general",
        "support_mode": "normal",
        "ambiguous": False,
        "routing_diagnostics": None,
        "context_resolution": empty_context_resolution(),
        "confidence": 0.0,
        "emotion": None,
        "previous_intent": None,
        "previous_emotion": None,
        "requires_past_memory": False,
        "should_save_episode": False,
        "short_term_memory_query": False,
        "has_fact_change": False,
        "record_type": None,
        "profile_changes": None,
        "is_today": None,
        "modify_target": None,
        "search_targets": [],
        "modify_plan_context": None,
        "profile_constraints": None,
        "retrieval_decision": None,
        "fast_intent_contract": None,
        "target_resource_context": None,
        "acsm_boundary": None,
        "diet_boundary": None,
        "search_results": [],
        "search_quality": "ok",
        "search_retry_count": 0,
        "search_query": None,
        "pending_writes": [],
        "awaiting_plan_confirmation": bool(active_proposal),
        "active_proposal": active_proposal,
        "pending_sequential_plan": None,
        "recent_dialogue": empty_recent_dialogue(),
        "draft_response": None,
        "draft_components": None,
        "proposed_plan": None,
        "proposed_plan_type": None,
        "proposed_plan_action": None,
        "home_recommendation_scope": None,
        "home_recommendations": None,
        "home_recommendation_recent": None,
        "intimacy_level": 1,
        "resolved_persona_id": None,
        "profile_sync_version": 0,
        "response": None,
        "force_regenerate": False,
        "validation_report": None,
        "validation_retry_count": 0,
        "generation_quality_flags": None,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
        "fallback_count": 0,
        "needs_clarification": False,
    }


async def run_fast_nodes(state: dict[str, Any], deps: FakeDeps) -> dict[str, Any]:
    for maker in (
        make_fast_router_node,
        make_fast_target_resource_node,
        make_fast_profile_constraints_node,
        make_fast_generate_node,
        make_fast_validate_node,
        make_fast_finalize_node,
    ):
        node = maker(deps)  # type: ignore[arg-type]
        state.update(await node(state))  # type: ignore[arg-type]
    return state


async def apply_writes_if_needed(state: dict[str, Any], deps: FakeDeps) -> dict[str, Any]:
    if state.get("intent") not in {INTENT_APPROVAL, INTENT_RECORD}:
        return {"write_succeeded": None, "pending": []}
    result = await execute_was_writes(
        deps=deps,  # type: ignore[arg-type]
        user_id=state["user_id"],
        intent=state.get("intent"),
        response=state.get("response") or "",
        record_type=state.get("record_type"),
        profile_changes=state.get("profile_changes"),
        today_plan=[],
        search_results=[],
        modify_target=state.get("modify_target"),
        modify_plan_context=state.get("modify_plan_context"),
        proposed_plan=state.get("proposed_plan"),
        proposed_plan_type=state.get("proposed_plan_type"),
        proposed_plan_action=state.get("proposed_plan_action"),
    )
    if result["write_succeeded"] and not result["pending"]:
        if state.get("intent") == INTENT_APPROVAL:
            state.update(sync_proposal_fields(None))
        if state.get("intent") == INTENT_RECORD and state.get("record_type") == "plan_delete":
            state.update(sync_proposal_fields(None))
    return result


async def run_profile(profile: dict[str, Any]) -> dict[str, Any]:
    was = FakeWAS()
    deps = FakeDeps(was)
    active_proposal: dict[str, Any] | None = None
    turn_results: list[dict[str, Any]] = []

    for index, turn in enumerate(TURNS, start=1):
        previous_active = deepcopy(active_proposal)
        state = initial_state(profile, turn["message"], previous_active, index)
        await run_fast_nodes(state, deps)
        next_active = evolve_active_proposal(previous_active, state)  # type: ignore[arg-type]
        state.update(sync_proposal_fields(next_active))  # type: ignore[arg-type]
        write_result = await apply_writes_if_needed(state, deps)
        active_proposal = deepcopy(state.get("active_proposal"))

        checks = evaluate_turn(
            label=turn["label"],
            state=state,
            profile=profile,
            was=was,
            write_result=write_result,
            previous_active=previous_active,
        )
        turn_results.append(
            {
                "turn": index,
                "label": turn["label"],
                "message": turn["message"],
                "expected": EXPECTED_LABELS[turn["label"]],
                "passed": checks["passed"],
                "checks": checks["checks"],
                "response_preview": (state.get("response") or "")[:240],
                "intent": state.get("intent"),
                "action_intent": state.get("action_intent"),
                "domain": state.get("domain"),
                "proposed_plan_type": state.get("proposed_plan_type"),
                "proposed_plan_count": len(state.get("proposed_plan") or []),
                "was_workout_count": len(was.workout_items),
                "was_diet_count": len(was.diet_items),
            }
        )

    return {
        "profile_id": profile["profile_id"],
        "selected_ai_persona": profile.get("selected_ai_persona"),
        "passed": all(item["passed"] for item in turn_results),
        "turns": turn_results,
    }


def evaluate_turn(
    *,
    label: str,
    state: dict[str, Any],
    profile: dict[str, Any],
    was: FakeWAS,
    write_result: dict[str, Any],
    previous_active: dict[str, Any] | None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    proposed = state.get("proposed_plan") or []
    constraints = state.get("profile_constraints") or {}

    def add(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    if label == "diet_week_create":
        add("action_create", state.get("action_intent") == "create")
        add("type_diet", state.get("proposed_plan_type") == "diet")
        add("diet_item_count_21", len(proposed) == 21)
        add("three_slots_each_day", diet_slots_complete(proposed))
        add("no_forbidden_food", no_forbidden_terms(proposed, constraints.get("food_forbidden_terms") or []))
        add("awaiting_confirmation", bool(state.get("active_proposal")))
    elif label == "diet_breakfast_modify":
        breakfasts = [item for item in proposed if item.get("plan_type") == "diet" and item.get("name") == "Breakfast"]
        add("action_modify", state.get("action_intent") == "modify")
        add("item_count_preserved", len(proposed) == 21)
        add("breakfast_all_dates_modified", len(breakfasts) == 7 and all("현미죽" in str(item.get("detail")) for item in breakfasts))
        add("no_forbidden_food", no_forbidden_terms(proposed, constraints.get("food_forbidden_terms") or []))
    elif label == "diet_followup_question":
        add("info_turn", state.get("action_intent") == "info")
        add("active_proposal_remains", bool(state.get("active_proposal") or previous_active))
    elif label == "diet_approval_write":
        add("approval_turn", state.get("action_intent") == "approval")
        add("write_succeeded", write_result.get("write_succeeded") is True)
        add("diet_was_count_21", len(was.diet_items) == 21)
        add("active_cleared", state.get("active_proposal") is None)
    elif label == "workout_week_create":
        add("action_create", state.get("action_intent") == "create")
        add("type_workout", state.get("proposed_plan_type") == "workout")
        add("workout_item_count_7", len(proposed) == 7)
        add("no_forbidden_workout", no_forbidden_terms(proposed, constraints.get("workout_forbidden_terms") or []))
        add("stretching_not_cardio", stretching_not_cardio(proposed))
    elif label == "workout_knee_modify":
        add("action_modify", state.get("action_intent") == "modify")
        add("item_count_preserved", len(proposed) == 7)
        add("no_forbidden_workout", no_forbidden_terms(proposed, constraints.get("workout_forbidden_terms") or []))
    elif label == "workout_followup_question":
        add("info_turn", state.get("action_intent") == "info")
        add("active_proposal_remains", bool(state.get("active_proposal") or previous_active))
    elif label == "workout_approval_write":
        add("approval_turn", state.get("action_intent") == "approval")
        add("write_succeeded", write_result.get("write_succeeded") is True)
        add("workout_was_count_7", len(was.workout_items) == 7)
        add("active_cleared", state.get("active_proposal") is None)
    elif label == "bundle_week_create":
        add("action_create", state.get("action_intent") == "create")
        add("type_bundle", state.get("proposed_plan_type") == "bundle")
        add("bundle_item_count_28", len(proposed) == 28)
        add("bundle_has_7_workouts", sum(1 for item in proposed if item.get("plan_type") == "workout") == 7)
        add("bundle_has_21_diets", sum(1 for item in proposed if item.get("plan_type") == "diet") == 21)
        add("three_slots_each_day", diet_slots_complete(proposed))
    elif label == "delete_all_calendar":
        add("record_delete", state.get("action_intent") == "record" and state.get("record_type") == "plan_delete")
        add("delete_payload_all", (state.get("profile_changes") or {}).get("plan_type") == "all" and (state.get("profile_changes") or {}).get("target_scope") == "all")
        add("write_succeeded", write_result.get("write_succeeded") is True)
        add("was_empty", len(was.workout_items) == 0 and len(was.diet_items) == 0)
        add("active_cleared", state.get("active_proposal") is None)

    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def diet_slots_complete(items: list[dict[str, Any]]) -> bool:
    by_day: dict[str, set[str]] = {}
    for item in items:
        if item.get("plan_type") != "diet":
            continue
        by_day.setdefault(str(item.get("day")), set()).add(str(item.get("name")))
    return bool(by_day) and all(slots == {"Breakfast", "Lunch", "Dinner"} for slots in by_day.values())


def no_forbidden_terms(items: list[dict[str, Any]], forbidden_terms: list[str]) -> bool:
    text = json.dumps(items, ensure_ascii=False)
    return not any(term and term in text for term in forbidden_terms)


def stretching_not_cardio(items: list[dict[str, Any]]) -> bool:
    for item in items:
        if item.get("plan_type") != "workout":
            continue
        text = f"{item.get('name')} {item.get('detail')}"
        if "스트레칭" in text and item.get("name") == "유산소":
            return False
    return True


async def main() -> None:
    profile_results = [await run_profile(profile) for profile in PROFILES]
    total_turns = sum(len(profile["turns"]) for profile in profile_results)
    passed_turns = sum(1 for profile in profile_results for turn in profile["turns"] if turn["passed"])
    failed_turns = [
        {
            "profile_id": profile["profile_id"],
            "label": turn["label"],
            "checks": [check for check in turn["checks"] if not check["passed"]],
            "response_preview": turn["response_preview"],
        }
        for profile in profile_results
        for turn in profile["turns"]
        if not turn["passed"]
    ]
    result = {
        "suite": "fast_plan_flow_10_profiles",
        "labels": EXPECTED_LABELS,
        "summary": {
            "profiles": len(PROFILES),
            "turns_per_profile": len(TURNS),
            "total_turns": total_turns,
            "passed_turns": passed_turns,
            "failed_turns": len(failed_turns),
            "accuracy": round(passed_turns / total_turns, 4) if total_turns else 0,
        },
        "failed_turns": failed_turns,
        "profiles": profile_results,
    }
    output_path = ROOT / "fast_plan_flow_results.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if failed_turns:
        print(json.dumps(failed_turns[:5], ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
