from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.trace_store import TraceStore
from app.graph.nodes.context_resolver import make_context_resolver_node
from app.graph.nodes.intent import make_intent_node
from app.routers.chat import _build_initial_state
from app.schemas.chat import ChatRequest


@dataclass
class Case:
    group: str
    name: str
    message: str
    expected_action: str
    expected_domain: str | None = None
    expected_support: str = "normal"
    expected_record_type: str | None = None
    active_proposal_domain: str | None = None
    active_proposal_write_mode: str = "create"
    active_proposal_summary: str | None = None
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    expected_reference: str | None = None
    expect_save_episode: bool | None = None


class FakeRouter:
    async def generate(self, *, system_prompt: str, user_content: str, response_schema):  # noqa: ANN001
        schema_name = getattr(response_schema, "__name__", "")
        if schema_name == "PlanConfirmationDecision":
            message = _extract_section(user_content, "[User Message]").lower()
            approved = (
                any(token in message for token in ("좋아", "진행", "적용", "반영", "그대로", "응", "네", "오케이", "확인"))
                and not any(token in message for token in ("바꿔", "수정", "변경", "조정", "덜", "빼", "추가", "제외"))
            )
            return json.dumps(
                {
                    "approved": approved,
                    "confidence": 0.96 if approved else 0.18,
                    "reason": "fake_router_confirmation",
                },
                ensure_ascii=False,
            )

        message = _extract_resolved_message(user_content)
        normalized = message.lower()
        emotion_label = "중립"
        emotion_intensity = 0.0
        if any(token in normalized for token in ("지쳐", "힘들", "불안", "우울", "외롭", "걱정", "스트레스", "버겁")):
            emotion_label = "불안"
            emotion_intensity = 0.72

        if any(token in normalized for token in ("운동 체크", "운동 완료", "식단 체크", "식단 완료", "먹었어", "완료했어", "체크했어")):
            return _intent_json(
                intent="기록",
                confidence=0.93,
                emotion_label=emotion_label,
                emotion_intensity=emotion_intensity,
                record_type="plan_check",
                is_today=True,
            )

        if any(token in normalized for token in ("왜", "뭐야", "무엇", "언제", "얼마나", "궁금", "대신 뭐")):
            return _intent_json(
                intent="정보",
                confidence=0.91,
                emotion_label=emotion_label,
                emotion_intensity=emotion_intensity,
                search_targets=["vdb_external"],
            )

        if any(token in normalized for token in ("외로워", "외롭", "주말 잘 보내", "별일 없지")):
            return _intent_json(
                intent="casual",
                confidence=0.84,
                emotion_label=emotion_label,
                emotion_intensity=emotion_intensity,
            )

        return _intent_json(
            intent="fallback",
            confidence=0.3,
            emotion_label=emotion_label,
            emotion_intensity=emotion_intensity,
        )


def _intent_json(
    *,
    intent: str,
    confidence: float,
    emotion_label: str,
    emotion_intensity: float,
    record_type: str | None = None,
    is_today: bool | None = None,
    search_targets: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "intent": intent,
            "confidence": confidence,
            "emotion": {"label": emotion_label, "intensity": emotion_intensity},
            "has_fact_change": False,
            "requires_past_memory": False,
            "should_save_episode": False,
            "record_type": record_type,
            "profile_changes": None,
            "is_today": is_today,
            "modify_target": None,
            "search_targets": search_targets or [],
        },
        ensure_ascii=False,
    )


def _extract_section(content: str, marker: str) -> str:
    idx = content.find(marker)
    if idx < 0:
        return ""
    segment = content[idx + len(marker) :].strip()
    lines = segment.splitlines()
    return lines[0].strip() if lines else segment.strip()


def _extract_resolved_message(content: str) -> str:
    resolved_marker = "[Resolved User Message]"
    original_marker = "[Original User Message]"
    if resolved_marker in content:
        return content.split(resolved_marker, 1)[1].strip().splitlines()[0].strip()
    if original_marker in content:
        return content.split(original_marker, 1)[1].strip().splitlines()[0].strip()
    return content.strip().splitlines()[-1].strip()


def _build_case_state(case: Case) -> dict[str, Any]:
    state = _build_initial_state(ChatRequest(user_id="intent_test_user", user_message=case.message))
    state["turn_count"] = 3
    if case.recent_turns:
        state["recent_dialogue"] = {"recent_turns": case.recent_turns}
        state["previous_intent"] = case.recent_turns[-1].get("action_intent")
    if case.active_proposal_domain:
        state["active_proposal"] = {
            "domain": case.active_proposal_domain,
            "write_mode": case.active_proposal_write_mode,
            "items": [{"name": "테스트 계획"}],
            "summary": case.active_proposal_summary or f"{case.active_proposal_domain} 제안",
            "last_used_turn": 2,
        }
        state["awaiting_plan_confirmation"] = True
        state["proposed_plan"] = [{"name": "테스트 계획"}]
        state["proposed_plan_type"] = case.active_proposal_domain
        state["proposed_plan_action"] = case.active_proposal_write_mode
    return state


def _recent_turn(
    *,
    user_text: str,
    assistant_text: str,
    action_intent: str,
    domain: str,
    support_mode: str = "normal",
    referenced_object: str = "none",
    state_effect: str = "none",
) -> dict[str, Any]:
    return {
        "turn_id": 2,
        "user_text": user_text,
        "assistant_text": assistant_text,
        "user_summary": user_text[:100],
        "assistant_summary": assistant_text[:100],
        "action_intent": action_intent,
        "domain": domain,
        "support_mode": support_mode,
        "referenced_object": referenced_object,
        "state_effect": state_effect,
    }


CASES: list[Case] = [
    Case("create", "create_workout_basic", "오늘 운동 계획 짜줘", "create", "workout"),
    Case("create", "create_diet_basic", "저녁 식단 짜줘", "create", "diet"),
    Case("create", "create_workout_injury", "무릎 안 좋은데 하체 운동 루틴 만들어줘", "create", "workout"),
    Case("create", "create_diet_allergy", "갑각류 알레르기 있는데 식단 계획 짜줘", "create", "diet"),
    Case("create", "create_workout_care", "요즘 너무 지쳐서 부담 없는 운동 계획 짜줘", "create", "workout", "care"),
    Case("create", "create_light_workout_plan", "운동을 가볍게 잡아줘", "create", "workout"),
    Case("create", "create_weekly_workout_plan", "일주일 운동 플랜을 내 상태에 맞춰서 짜줘", "create", "workout"),
    Case("create", "create_weekly_diet_plan", "일주일 식단 플랜을 내 상태에 맞춰서 작성해줘", "create", "diet"),
    Case("create", "create_mixed_workout_diet_plan", "운동이나 식단을 가볍게 잡아줘", "create", "general"),
    Case("create", "create_diet_goal_workout_plan", "다이어트 운동 플랜 작성해줘", "create", "workout"),

    Case("modify", "modify_active_workout", "그거 좀 덜 빡세게 바꿔줘", "modify", "workout", active_proposal_domain="workout", expected_reference="active_proposal"),
    Case("modify", "modify_active_diet", "방금 식단에서 아침만 바꿔줘", "modify", "diet", active_proposal_domain="diet", expected_reference="active_proposal"),
    Case("modify", "modify_active_workout_safety_realistic", "방금 운동 계획을 무릎 통증과 18분 제한에 맞게 더 안전하고 현실적으로 수정해줘", "modify", "workout", active_proposal_domain="workout", expected_reference="active_proposal"),
    Case("modify", "modify_active_workout_10min", "오늘은 시간이 없어서 10분 안에 가능한 버전으로 줄여줘", "modify", "workout", active_proposal_domain="workout", expected_reference="active_proposal"),
    Case("modify", "modify_active_workout_week_scope", "일주일 운동 플랜이라니까? 왜 5월 22일만 추천해?", "modify", "workout", active_proposal_domain="workout"),
    Case("modify", "modify_workout_explicit", "운동 계획에서 하체는 빼줘", "modify", "workout"),
    Case("modify", "modify_diet_explicit", "식단에서 유제품 제외해줘", "modify", "diet"),
    Case("modify", "modify_diet_care", "요즘 너무 힘들어서 식단 좀 가볍게 조정해줘", "modify", "diet", "care"),

    Case("info", "info_diet_amount", "아침 식단에서 단백질 얼마나 먹어야 해?", "info", "diet"),
    Case("info", "info_workout_alternative", "무릎 통증 있을 때 스쿼트 대신 뭐가 좋아?", "info", "workout"),
    Case(
        "info",
        "info_previous_answer",
        "왜 그렇게 짰어?",
        "info",
        "workout",
        recent_turns=[_recent_turn(user_text="운동 계획 짜줘", assistant_text="하체 운동 계획을 제안할게요.", action_intent="create", domain="workout", state_effect="proposal_created")],
        expected_reference="previous_answer",
    ),
    Case("info", "info_general_definition", "칼로리 적자가 뭐야?", "info", "diet"),
    Case("info", "info_care_overlay", "요즘 너무 불안한데 운동 쉬어도 되는지 궁금해", "info", "workout", "care"),

    Case("record", "record_weight", "내 체중 72kg로 기록해줘", "record", "profile", expected_record_type="profile"),
    Case("record", "record_allergy", "갑각류 알레르기 추가해줘", "record", "profile", expected_record_type="profile"),
    Case("record", "record_workout_check", "오늘 운동 체크했어", "record", "workout", expected_record_type="plan_check"),
    Case("record", "record_goal", "내 목표를 체지방 감량으로 변경해줘", "record", "profile", expected_record_type="profile"),
    Case("record", "record_diet_check", "오늘 식단 체크했어", "record", "diet", expected_record_type="plan_check"),
    Case("record", "record_workout_delete", "오늘 운동 플랜 삭제해줘", "record", "workout", expected_record_type="plan_delete"),

    Case("approval", "approval_basic", "좋아 그걸로 진행해줘", "approval", "workout", active_proposal_domain="workout", expected_reference="active_proposal"),
    Case("approval", "approval_apply_plan", "이 계획으로 적용해줘", "approval", "diet", active_proposal_domain="diet", expected_reference="active_proposal"),
    Case("approval", "approval_short_ack", "응", "approval", "workout", active_proposal_domain="workout"),
    Case("approval", "approval_modified_plan", "방금 수정한 계획 반영해줘", "approval", "workout", active_proposal_domain="workout", active_proposal_write_mode="update", expected_reference="active_proposal"),
    Case("approval", "approval_keep_same", "그대로 해줘", "approval", "diet", active_proposal_domain="diet", expected_reference="active_proposal"),

    Case("casual", "casual_hello", "안녕", "casual", "general"),
    Case("casual", "casual_thanks", "고마워", "casual", "general"),
    Case("casual", "casual_care_lonely", "오늘 너무 외로워", "care", "general", "care"),
    Case("casual", "casual_weekend", "주말 잘 보내", "casual", "general"),
    Case("casual", "casual_memory_save", "내 별명은 코코야 기억해줘", "casual", "general", expect_save_episode=True),

    Case("safety", "safety_self_harm", "죽고 싶어", "safety", "general"),
    Case("safety", "safety_chest_pain", "가슴이 너무 조여와", "safety", "general"),
    Case("safety", "safety_chest_sweat_continue", "운동하다가 가슴이 답답하고 식은땀이 나. 계속해도 돼?", "safety", "general"),
    Case("safety", "safety_breathing", "숨이 너무 차고 어지러워", "safety", "general"),
    Case("safety", "safety_overdose", "약을 너무 많이 먹은 것 같아", "safety", "general"),
    Case("safety", "safety_allergy", "심한 알레르기 반응이 있는 것 같아", "safety", "general"),
]


async def run_case(case: Case, context_node, intent_node) -> tuple[bool, str]:
    state = _build_case_state(case)
    state.update(await context_node(state))
    result = await intent_node(state)

    errors: list[str] = []
    if result.get("action_intent") != case.expected_action:
        errors.append(f"action_intent={result.get('action_intent')} expected={case.expected_action}")
    if case.expected_domain and result.get("domain") != case.expected_domain:
        errors.append(f"domain={result.get('domain')} expected={case.expected_domain}")
    if result.get("support_mode") != case.expected_support:
        errors.append(f"support_mode={result.get('support_mode')} expected={case.expected_support}")
    if case.expected_record_type and result.get("record_type") != case.expected_record_type:
        errors.append(f"record_type={result.get('record_type')} expected={case.expected_record_type}")
    if case.expected_reference and state.get("context_resolution", {}).get("resolved_reference") != case.expected_reference:
        errors.append(
            f"resolved_reference={state.get('context_resolution', {}).get('resolved_reference')} expected={case.expected_reference}"
        )
    if case.expect_save_episode is not None and bool(result.get("should_save_episode")) != case.expect_save_episode:
        errors.append(f"should_save_episode={result.get('should_save_episode')} expected={case.expect_save_episode}")

    detail = (
        f"{case.group}/{case.name} | action={result.get('action_intent')} domain={result.get('domain')} "
        f"support={result.get('support_mode')} ref={state.get('context_resolution', {}).get('resolved_reference')}"
    )
    if errors:
        return False, detail + " | " + " ; ".join(errors)
    return True, detail


async def main() -> None:
    deps = SimpleNamespace(trace=TraceStore(), router=FakeRouter())
    context_node = make_context_resolver_node(deps)
    intent_node = make_intent_node(deps)

    grouped_results: dict[str, list[tuple[bool, str]]] = {}
    for case in CASES:
        ok, detail = await run_case(case, context_node, intent_node)
        grouped_results.setdefault(case.group, []).append((ok, detail))

    total = 0
    failed = 0
    for group, results in grouped_results.items():
        passed = sum(1 for ok, _ in results if ok)
        total += len(results)
        failed += len(results) - passed
        print(f"[{group}] {passed}/{len(results)} passed")
        for ok, detail in results:
            prefix = "PASS" if ok else "FAIL"
            print(f"  {prefix} {detail}")

    print(f"TOTAL {total - failed}/{total} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
