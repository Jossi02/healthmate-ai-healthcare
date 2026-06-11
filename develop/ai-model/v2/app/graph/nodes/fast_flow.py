"""Fast health-plan flow nodes.

This module intentionally keeps the demo runtime small:
- no Pinecone/RAG search path
- date/domain/action routing first
- deterministic profile boundaries before generation
- concise persona finalization after validation
"""
from __future__ import annotations

import re
import time
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.intents import (
    INTENT_APPROVAL,
    INTENT_CARE,
    INTENT_CASUAL,
    INTENT_FALLBACK,
    INTENT_INFO,
    INTENT_MODIFY,
    INTENT_PLAN,
    INTENT_RECORD,
    INTENT_SAFETY,
)
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

KST = ZoneInfo("Asia/Seoul")

WORKOUT_DOMAINS = {"workout"}
DIET_DOMAINS = {"diet"}
PLAN_DOMAINS = {"workout", "diet"}

WORKOUT_CATEGORIES = {
    "cardio": "유산소",
    "stretching": "스트레칭",
    "upper": "상체",
    "lower": "하체",
    "core": "코어",
    "full_body": "전신",
    "rest": "휴식",
}

MEAL_SLOTS = {
    "breakfast": "아침",
    "lunch": "점심",
    "dinner": "저녁",
}

PERSONA_ALIASES = {
    "default": "cheer_sis",
    "warm": "cheer_sis",
    "buddy": "playful_buddy",
    "soft_senior": "kind_younger",
    "science_coach": "routine_master",
}

PERSONA_SPECS = {
    "cheer_sis": {
        "label": "응원 누나",
        "speech": "casual",
        "intro": "좋아, 누나가",
        "plan_tail": "이대로 작성할까?",
        "done": "좋아, 캘린더에 반영했어.",
        "delete_done": "좋아, 요청한 범위는 깔끔하게 삭제할게.",
    },
    "kind_younger": {
        "label": "다정 동생",
        "speech": "polite",
        "intro": "좋아요, 제가",
        "plan_tail": "이대로 작성해도 괜찮을까요?",
        "done": "좋아요, 캘린더에 반영하겠습니다.",
        "delete_done": "요청하신 범위는 삭제하겠습니다.",
    },
    "strict_trainer": {
        "label": "직진 PT쌤",
        "speech": "casual",
        "intro": "좋아, 바로",
        "plan_tail": "이대로 갈까?",
        "done": "확인. 캘린더에 반영했어.",
        "delete_done": "확인. 요청한 범위는 삭제한다.",
    },
    "playful_buddy": {
        "label": "운동 메이트",
        "speech": "casual",
        "intro": "좋아, 같이",
        "plan_tail": "이대로 가보자?",
        "done": "좋아, 같이 갈 플랜으로 반영했어.",
        "delete_done": "좋아, 요청한 건 비워둘게.",
    },
    "routine_master": {
        "label": "루틴 장인",
        "speech": "polite",
        "intro": "좋습니다, 루틴 흐름에 맞춰",
        "plan_tail": "이 구성으로 루틴을 고정할까요?",
        "done": "좋습니다, 루틴에 반영했습니다.",
        "delete_done": "확인했습니다. 요청한 루틴 범위는 삭제하겠습니다.",
    },
    "daily_manager": {
        "label": "생활 매니저",
        "speech": "polite",
        "intro": "확인했습니다. 일정 기준으로",
        "plan_tail": "이 항목으로 캘린더에 작성할까요?",
        "done": "확인했습니다. 캘린더에 반영했습니다.",
        "delete_done": "확인했습니다. 요청 범위 삭제로 처리하겠습니다.",
    },
}

WORKOUT_KEYWORDS = (
    "운동",
    "헬스",
    "루틴",
    "유산소",
    "근력",
    "스트레칭",
    "상체",
    "하체",
    "코어",
    "걷기",
    "산책",
    "스쿼트",
    "푸시업",
    "플랭크",
)
DIET_KEYWORDS = (
    "식단",
    "식사",
    "밥",
    "메뉴",
    "아침",
    "점심",
    "저녁",
    "간식",
    "칼로리",
    "단백질",
    "음식",
    "먹",
)
PLAN_REQUEST_KEYWORDS = (
    "짜줘",
    "짤래",
    "작성",
    "만들",
    "추천",
    "구성",
    "플랜",
    "계획",
    "루틴",
)
MODIFY_KEYWORDS = (
    "수정",
    "바꿔",
    "변경",
    "교체",
    "빼",
    "제외",
    "추가",
    "늘려",
    "줄여",
    "가볍게",
    "부담",
    "마음에 안",
)
DELETE_KEYWORDS = ("삭제", "지워", "제거", "비워", "초기화", "없애")
APPROVAL_KEYWORDS = ("응", "네", "좋아", "오케이", "ok", "확정", "승인", "반영", "적용", "저장", "작성해")
COMMIT_KEYWORDS = ("반영", "적용", "저장", "캘린더", "작성해")
SAFETY_KEYWORDS = ("죽고", "자해", "가슴 통증", "실신", "호흡곤란", "피 토", "응급")
CARE_KEYWORDS = ("힘들", "지쳤", "우울", "불안", "무기력", "스트레스", "위로")
INFO_KEYWORDS = ("왜", "이유", "근거", "어떻게", "괜찮", "충분", "설명", "?")
ALL_SCOPE_KEYWORDS = ("모든", "모두", "전체", "다", "전부", "캘린더 내용", "현재 캘린더")


def make_fast_router_node(deps: NodeDeps):
    async def fast_router_node(state: GraphState) -> dict[str, Any]:
        started_at = time.perf_counter()
        message = str(state.get("user_message") or "").strip()
        active = _safe_dict(state.get("active_proposal"))
        contract = _route_message(message, state)
        intent, action_intent, domain = _state_labels_from_contract(contract, active)

        updates: dict[str, Any] = {
            "fast_intent_contract": contract,
            "intent": intent,
            "action_intent": action_intent,
            "domain": domain,
            "support_mode": "care" if action_intent == "care" else "normal",
            "ambiguous": bool(contract.get("needs_clarification")),
            "needs_clarification": bool(contract.get("needs_clarification")),
            "confidence": float(contract.get("confidence") or 0.0),
            "routing_diagnostics": {
                "route_kind": contract.get("route_kind"),
                "actions": contract.get("actions") or [],
                "reason": contract.get("reason", ""),
            },
            "record_type": None,
            "profile_changes": None,
            "modify_target": None,
        }

        if action_intent == "record":
            action = _first_action(contract)
            if action and action.get("operation") == "plan.delete":
                updates["record_type"] = "plan_delete"
                updates["modify_target"] = action.get("domain")
                updates["profile_changes"] = _delete_payload_from_action(action)

        deps.trace.record_current_event(
            stage="fast_router",
            status="ok",
            title="Fast intent routing completed",
            detail={
                "route_kind": contract.get("route_kind"),
                "intent": intent,
                "action_intent": action_intent,
                "domain": domain,
                "actions": len(contract.get("actions") or []),
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return updates

    return fast_router_node


def make_fast_target_resource_node(deps: NodeDeps):
    async def fast_target_resource_node(state: GraphState) -> dict[str, Any]:
        started_at = time.perf_counter()
        contract = _safe_dict(state.get("fast_intent_contract"))
        actions = _safe_list(contract.get("actions"))
        resource_actions: list[dict[str, Any]] = []

        for action in actions:
            if not isinstance(action, dict):
                continue
            source = str(action.get("target_source") or "unknown")
            operation = str(action.get("operation") or "")
            domain = str(action.get("domain") or "")
            target = _safe_dict(action.get("target"))
            dates = _safe_str_list(target.get("dates"))
            resource_target = _resource_slice_target(operation, target)
            items: list[dict[str, Any]] = []

            if operation in {"plan.modify", "plan.approve"}:
                if source in {"active_proposal", "unknown"}:
                    active = _safe_dict(state.get("active_proposal"))
                    if active.get("items"):
                        items = _slice_plan_items(active.get("items"), domain=domain, dates=dates, target=resource_target)
                        source = "active_proposal"
                if not items and source in {"saved_planner", "unknown"} and domain in PLAN_DOMAINS:
                    items = await _load_saved_planner_slice(deps, state, domain=domain, dates=dates, target=resource_target)
                    if items:
                        source = "saved_planner"

            resource_actions.append(
                {
                    "action": action,
                    "source": source,
                    "items": items,
                    "item_count": len(items),
                }
            )

        context = {
            "actions": resource_actions,
            "active_proposal": state.get("active_proposal"),
        }
        deps.trace.record_current_event(
            stage="target_resource",
            status="ok",
            title="Target resources prepared",
            detail={"actions": len(resource_actions), "items": sum(a["item_count"] for a in resource_actions)},
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return {"target_resource_context": context}

    return fast_target_resource_node


def make_fast_profile_constraints_node(deps: NodeDeps):
    async def fast_profile_constraints_node(state: GraphState) -> dict[str, Any]:
        profile = _effective_profile(state)
        constraints = build_profile_constraints(profile)
        return {
            "profile_constraints": constraints,
            "acsm_boundary": build_acsm_boundary(profile, constraints),
            "diet_boundary": build_diet_boundary(profile, constraints),
        }

    return fast_profile_constraints_node


def make_fast_generate_node(deps: NodeDeps):
    async def fast_generate_node(state: GraphState) -> dict[str, Any]:
        started_at = time.perf_counter()
        contract = _safe_dict(state.get("fast_intent_contract"))
        persona_id = _resolve_persona_id(_effective_profile(state))
        actions = [action for action in _safe_list(contract.get("actions")) if isinstance(action, dict)]
        route_kind = str(contract.get("route_kind") or "")

        if state.get("needs_clarification") or route_kind == "clarify":
            response = _persona_clarify(
                persona_id,
                str(contract.get("clarification_question") or "운동 플랜인지 식단 플랜인지 먼저 알려줘."),
            )
            return _generation_updates(state, response=response, persona_id=persona_id)

        if route_kind in {"chat", "care", "info", "safety", "fallback"}:
            response = _build_non_plan_response(state, contract, persona_id)
            return _generation_updates(state, response=response, persona_id=persona_id)

        if actions and all(action.get("operation") == "plan.delete" for action in actions):
            action = actions[0]
            response = _build_delete_response(action, persona_id)
            return _generation_updates(
                state,
                response=response,
                persona_id=persona_id,
                proposed_plan=[],
                proposed_plan_type=None,
                proposed_plan_action=None,
            )

        if actions and all(action.get("operation") == "plan.approve" for action in actions):
            active = _safe_dict(state.get("active_proposal"))
            items = _safe_plan_items(active.get("items"))
            if not items:
                response = _persona_clarify(persona_id, "지금 승인할 플랜이 없어요. 먼저 운동이나 식단 플랜을 요청해줘.")
                return _generation_updates(state, response=response, persona_id=persona_id)
            domain = _proposal_domain_from_items(items, str(active.get("domain") or "workout"))
            response = _persona_done(persona_id, domain=domain)
            return _generation_updates(
                state,
                response=response,
                persona_id=persona_id,
                intent=INTENT_APPROVAL,
                action_intent="approval",
                proposed_plan=items,
                proposed_plan_type=domain,
                proposed_plan_action=str(active.get("write_mode") or "create"),
                awaiting_plan_confirmation=False,
            )

        proposed_items: list[dict[str, Any]] = []
        write_mode = "create"
        domains: list[str] = []
        has_modify_action = False
        resource_context = _safe_dict(state.get("target_resource_context"))
        resource_actions = _safe_list(resource_context.get("actions"))
        resource_by_index = {
            index: item for index, item in enumerate(resource_actions) if isinstance(item, dict)
        }
        commit_now = False

        for index, action in enumerate(actions):
            operation = str(action.get("operation") or "")
            domain = str(action.get("domain") or "")
            if domain not in PLAN_DOMAINS:
                continue
            domains.append(domain)
            commit_now = commit_now or bool(action.get("commit"))

            if operation == "plan.create":
                proposed_items.extend(_create_plan_items_for_action(state, action))
                write_mode = "create" if write_mode != "update" else write_mode
            elif operation == "plan.modify":
                has_modify_action = True
                resource = _safe_dict(resource_by_index.get(index))
                base_items = _safe_plan_items(resource.get("items"))
                if not base_items:
                    active = _safe_dict(state.get("active_proposal"))
                    target = _safe_dict(action.get("target"))
                    base_items = _slice_plan_items(
                        active.get("items"),
                        domain=domain,
                        dates=_safe_str_list(target.get("dates")),
                        target=_resource_slice_target(operation, target),
                    )
                if not base_items:
                    base_items = _create_plan_items_for_action(state, action)
                proposed_items.extend(_modify_plan_items_for_action(state, action, base_items))
                source = str(resource.get("source") or action.get("target_source") or "")
                active_mode = str(_safe_dict(state.get("active_proposal")).get("write_mode") or "")
                write_mode = "update" if source == "saved_planner" or active_mode == "update" else "create"

        proposed_items = _dedupe_plan_items(proposed_items)
        proposed_type = _proposal_domain_from_items(proposed_items, domains[0] if domains else "workout")
        if len(set(domains)) > 1:
            proposed_type = "bundle"

        if not proposed_items:
            response = _persona_clarify(persona_id, "날짜와 운동/식단 종류가 조금 애매해. 예: 이번 주 운동 플랜 짜줘처럼 말해줘.")
            return _generation_updates(state, response=response, persona_id=persona_id)

        response = _build_plan_preview_response(
            state=state,
            persona_id=persona_id,
            items=proposed_items,
            proposed_type=proposed_type,
            write_mode="update" if has_modify_action else write_mode,
            commit_now=commit_now,
        )
        intent = INTENT_APPROVAL if commit_now else (INTENT_MODIFY if has_modify_action else INTENT_PLAN)
        action_intent = "approval" if commit_now else ("modify" if has_modify_action else "create")

        deps.trace.record_current_event(
            stage="fast_generate",
            status="ok",
            title="Fast generation completed",
            detail={
                "items": len(proposed_items),
                "proposed_type": proposed_type,
                "write_mode": write_mode,
                "commit_now": commit_now,
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return _generation_updates(
            state,
            response=response,
            persona_id=persona_id,
            intent=intent,
            action_intent=action_intent,
            proposed_plan=proposed_items,
            proposed_plan_type=proposed_type,
            proposed_plan_action=write_mode,
            awaiting_plan_confirmation=not commit_now,
        )

    return fast_generate_node


def make_fast_validate_node(deps: NodeDeps):
    async def fast_validate_node(state: GraphState) -> dict[str, Any]:
        proposed = _safe_plan_items(state.get("proposed_plan"))
        if not proposed:
            return {"validation_report": {"passed": True, "issues": []}}

        contract = _safe_dict(state.get("fast_intent_contract"))
        constraints = _safe_dict(state.get("profile_constraints"))
        report = validate_plan(proposed, contract, constraints)
        if report["passed"]:
            return {"validation_report": report, "generation_quality_flags": {"fast_plan_valid": True}}

        repaired = repair_plan(proposed, constraints)
        repaired_report = validate_plan(repaired, contract, constraints)
        return {
            "proposed_plan": repaired,
            "validation_report": repaired_report,
            "generation_quality_flags": {
                "fast_plan_valid": bool(repaired_report["passed"]),
                "fast_plan_repaired": True,
                "fast_plan_initial_issues": report["issues"],
            },
        }

    return fast_validate_node


def make_fast_finalize_node(deps: NodeDeps):
    async def fast_finalize_node(state: GraphState) -> dict[str, Any]:
        response = str(state.get("response") or state.get("draft_response") or "").strip()
        if not response:
            response = "요청을 정확히 처리하지 못했어요. 운동 플랜인지 식단 플랜인지 한 번만 더 말해줘."
        return {
            "response": _trim_response(response),
            "draft_response": _trim_response(str(state.get("draft_response") or response)),
            "resolved_persona_id": state.get("resolved_persona_id") or _resolve_persona_id(_effective_profile(state)),
        }

    return fast_finalize_node


def _route_message(message: str, state: GraphState) -> dict[str, Any]:
    normalized = _normalize_text(message)
    active = _safe_dict(state.get("active_proposal"))

    if not normalized:
        return _clarify_contract("메시지가 비어 있어요. 운동이나 식단 중 필요한 걸 말해줘.")

    if any(keyword in normalized for keyword in SAFETY_KEYWORDS):
        return {"route_kind": "safety", "needs_clarification": False, "actions": [], "confidence": 0.95}

    mixed_actions = _mixed_delete_modify_actions(message, normalized)
    if mixed_actions:
        return {
            "route_kind": "bundle" if len(mixed_actions) > 1 else "single",
            "needs_clarification": False,
            "actions": mixed_actions,
            "confidence": 0.86,
        }

    if _looks_like_delete(normalized):
        domains = _detect_domains(normalized)
        if len(domains) != 1:
            domain = "all" if _has_any(normalized, ALL_SCOPE_KEYWORDS) or len(domains) > 1 else "all"
        else:
            domain = domains[0]
        scope = "all" if _has_any(normalized, ALL_SCOPE_KEYWORDS) else "day"
        dates = [] if scope == "all" else _extract_dates(message)
        return {
            "route_kind": "single",
            "needs_clarification": False,
            "actions": [
                _action(
                    operation="plan.delete",
                    domain=domain,
                    target_source="saved_planner",
                    dates=dates,
                    scope=scope,
                    request_detail={"instruction": message, "change_type": "delete", "preserve_others": False},
                    commit=True,
                    destructive=True,
                    requires_confirmation=False,
                )
            ],
            "confidence": 0.95,
        }

    if active and _looks_like_approval(normalized) and not _looks_like_modify(normalized):
        domain = str(active.get("domain") or "workout")
        if domain == "bundle":
            domains = _domains_from_items(active.get("items"))
            actions = [
                _action(
                    operation="plan.approve",
                    domain=domain_item,
                    target_source="active_proposal",
                    dates=_dates_from_items(active.get("items"), domain=domain_item),
                    scope="range",
                    request_detail={"instruction": message, "change_type": "approve", "preserve_others": True},
                    commit=True,
                )
                for domain_item in domains
            ]
        else:
            actions = [
                _action(
                    operation="plan.approve",
                    domain=domain if domain in PLAN_DOMAINS else "workout",
                    target_source="active_proposal",
                    dates=_dates_from_items(active.get("items"), domain=domain),
                    scope="range",
                    request_detail={"instruction": message, "change_type": "approve", "preserve_others": True},
                    commit=True,
                )
            ]
        return {"route_kind": "single" if len(actions) == 1 else "bundle", "needs_clarification": False, "actions": actions, "confidence": 0.96}

    domains = _detect_domains(normalized)
    wants_plan = _has_any(normalized, PLAN_REQUEST_KEYWORDS)
    wants_modify = _looks_like_modify(normalized)

    if wants_modify and _can_route_plan_modify_without_clarification(normalized, active, domains):
        if not domains:
            active_domain = str(active.get("domain") or "")
            domains = _domains_from_items(active.get("items")) if active_domain == "bundle" else [active_domain] if active_domain in PLAN_DOMAINS else []
        if not domains:
            return _clarify_contract("운동 플랜을 수정할지 식단 플랜을 수정할지 알려줘.")
        target_source = "active_proposal" if active else "saved_planner"
        target_dates = _extract_dates(message)
        commit = _has_any(normalized, COMMIT_KEYWORDS)
        actions = [
            _action(
                operation="plan.modify",
                domain=domain,
                target_source=target_source,
                dates=_resolve_modify_dates(message, active, domain, target_dates),
                scope="range" if len(_resolve_modify_dates(message, active, domain, target_dates)) > 1 else "day",
                target=_target_hints(normalized, _resolve_modify_dates(message, active, domain, target_dates)),
                request_detail={"instruction": message, "change_type": "modify", "preserve_others": True},
                commit=commit,
            )
            for domain in domains
        ]
        return {"route_kind": "single" if len(actions) == 1 else "bundle", "needs_clarification": False, "actions": actions, "confidence": 0.9}

    if wants_plan and domains:
        dates = _extract_dates(message)
        actions = [
            _action(
                operation="plan.create",
                domain=domain,
                target_source="new",
                dates=dates,
                scope="range" if len(dates) > 1 else "day",
                target=_target_hints(normalized, dates),
                request_detail={"instruction": message, "change_type": "create", "preserve_others": True},
                commit=False,
            )
            for domain in domains
        ]
        return {"route_kind": "single" if len(actions) == 1 else "bundle", "needs_clarification": False, "actions": actions, "confidence": 0.93}

    if wants_plan and not domains:
        return _clarify_contract("운동 플랜이 필요해, 식단 플랜이 필요해? 둘 다면 둘 다라고 말해줘.")

    if any(keyword in normalized for keyword in CARE_KEYWORDS):
        return {"route_kind": "care", "needs_clarification": False, "actions": [], "confidence": 0.85}

    if any(keyword in normalized for keyword in INFO_KEYWORDS):
        return {"route_kind": "info", "needs_clarification": False, "actions": [], "confidence": 0.82}

    return {"route_kind": "chat", "needs_clarification": False, "actions": [], "confidence": 0.75}


def _action(
    *,
    operation: str,
    domain: str,
    target_source: str,
    dates: list[str],
    scope: str,
    request_detail: dict[str, Any],
    commit: bool,
    destructive: bool = False,
    requires_confirmation: bool = False,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_target = {
        "dates": dates,
        "scope": scope,
        "meal_slots": [],
        "workout_categories": [],
        "food_keywords": [],
        "exercise_keywords": [],
    }
    if target:
        base_target.update(target)
        base_target["dates"] = dates or _safe_str_list(target.get("dates"))
        base_target["scope"] = target.get("scope") or scope
    return {
        "operation": operation,
        "domain": domain,
        "target_source": target_source,
        "target": base_target,
        "request_detail": request_detail,
        "commit": commit,
        "destructive": destructive,
        "requires_confirmation": requires_confirmation,
        "confidence": 0.9,
    }


def _clarify_contract(question: str) -> dict[str, Any]:
    return {
        "route_kind": "clarify",
        "needs_clarification": True,
        "clarification_question": question,
        "actions": [],
        "confidence": 0.55,
    }


def _mixed_delete_modify_actions(message: str, normalized: str) -> list[dict[str, Any]]:
    if not (_looks_like_delete(normalized) and _looks_like_modify(normalized)):
        return []

    domain_operations = {
        domain: operation
        for domain in PLAN_DOMAINS
        if (operation := _domain_operation_from_clauses(normalized, domain))
    }
    if len(domain_operations) < 2:
        return []
    if len(set(domain_operations.values())) < 2:
        return []

    dates = _extract_dates(message)
    scope = "range" if len(dates) > 1 else "day"
    commit = _has_any(normalized, COMMIT_KEYWORDS)
    actions: list[dict[str, Any]] = []
    for domain in ("workout", "diet"):
        operation = domain_operations.get(domain)
        if operation == "delete":
            actions.append(
                _action(
                    operation="plan.delete",
                    domain=domain,
                    target_source="saved_planner",
                    dates=dates,
                    scope=scope,
                    request_detail={"instruction": message, "change_type": "delete", "preserve_others": True},
                    commit=True,
                    destructive=True,
                    requires_confirmation=False,
                )
            )
        elif operation == "modify":
            actions.append(
                _action(
                    operation="plan.modify",
                    domain=domain,
                    target_source="saved_planner",
                    dates=dates,
                    scope=scope,
                    target=_target_hints(normalized, dates),
                    request_detail={"instruction": message, "change_type": "modify", "preserve_others": True},
                    commit=commit,
                )
            )
    return actions


def _domain_operation_from_clauses(normalized: str, domain: str) -> str | None:
    clauses = [piece.strip() for piece in re.split(r"하고|그리고|[,，;|/]", normalized) if piece.strip()]
    anchors = ("운동", "루틴", "상체", "하체", "유산소", "스트레칭") if domain == "workout" else (
        "식단",
        "식사",
        "아침",
        "점심",
        "저녁",
        "메뉴",
    )
    for clause in clauses:
        if not any(anchor in clause for anchor in anchors):
            continue
        has_delete = _looks_like_delete(clause)
        has_modify = _looks_like_modify(clause)
        if has_delete and not has_modify:
            return "delete"
        if has_modify and not has_delete:
            return "modify"
    return None


def _state_labels_from_contract(contract: dict[str, Any], active: dict[str, Any]) -> tuple[str, str, str]:
    route_kind = str(contract.get("route_kind") or "")
    if route_kind == "safety":
        return INTENT_SAFETY, "safety", "general"
    if route_kind == "care":
        return INTENT_CARE, "care", "general"
    if route_kind == "info":
        return INTENT_INFO, "info", _safe_domain(str(active.get("domain") or "general"))
    if route_kind == "chat":
        return INTENT_CASUAL, "casual", "general"
    if route_kind == "clarify":
        return INTENT_FALLBACK, "fallback", "general"

    action = _first_action(contract)
    if not action:
        return INTENT_FALLBACK, "fallback", "general"
    operation = str(action.get("operation") or "")
    domain = _safe_domain(str(action.get("domain") or "general"))
    if operation == "plan.delete":
        return INTENT_RECORD, "record", domain
    if operation == "plan.approve":
        return INTENT_APPROVAL, "approval", domain
    if operation == "plan.modify":
        return INTENT_MODIFY, "modify", domain
    if operation == "plan.create":
        return INTENT_PLAN, "create", domain
    return INTENT_FALLBACK, "fallback", "general"


def build_profile_constraints(profile: dict[str, Any]) -> dict[str, Any]:
    allergies = _profile_list(profile, "allergies", "allergy", "otherAllergy", "other_allergy")
    injuries = _profile_list(profile, "injury_history", "pain_points")
    medical = _profile_list(profile, "medical_history", "medical_conditions", "conditions")
    diet_type = _profile_text(profile, "diet_type", "dietary_preferences", "dietary_restrictions")
    goal = _profile_text(profile, "goal", "primary_goal", "exercise_goal", "diet_goal")
    activity_level = _profile_text(profile, "activity_level", "activityLevel", "exercise_level", "fitness_level")
    available_time = _profile_int(profile, "available_time_minutes", "available_time", default=30)

    food_forbidden: set[str] = set()
    for allergy in allergies:
        text = allergy.lower()
        if any(token in text for token in ("유제품", "우유", "dairy", "milk", "lactose")):
            food_forbidden.update({"우유", "요거트", "요구르트", "치즈", "버터", "크림", "유청"})
        if any(token in text for token in ("견과", "땅콩", "nut", "peanut")):
            food_forbidden.update({"견과", "땅콩", "아몬드", "호두"})
        if any(token in text for token in ("해산물", "새우", "shellfish", "seafood")):
            food_forbidden.update({"새우", "조개", "오징어", "해산물"})
        if any(token in text for token in ("계란", "달걀", "egg")):
            food_forbidden.update({"계란", "달걀", "오믈렛"})

    if any(token in diet_type.lower() for token in ("vegan", "비건")):
        food_forbidden.update({"닭", "소고기", "돼지", "연어", "참치", "고등어", "계란", "우유", "치즈", "요거트"})
    elif any(token in diet_type.lower() for token in ("vegetarian", "채식")):
        food_forbidden.update({"닭", "소고기", "돼지", "연어", "참치", "고등어"})

    workout_forbidden: set[str] = set()
    if any(_contains_any(item, ("무릎", "knee")) for item in injuries + medical):
        workout_forbidden.update({"점프", "버피", "러닝", "깊은 런지", "고강도 인터벌"})
    if any(_contains_any(item, ("허리", "디스크", "back")) for item in injuries + medical):
        workout_forbidden.update({"데드리프트", "윗몸일으키기", "무거운 힌지", "점프"})
    if any(_contains_any(item, ("어깨", "shoulder")) for item in injuries + medical):
        workout_forbidden.update({"오버헤드 프레스", "딥스", "무거운 숄더프레스"})
    if any(_contains_any(item, ("고혈압", "hypertension", "혈압")) for item in medical):
        workout_forbidden.update({"숨참기", "최대중량", "고강도 인터벌"})
    if any(_contains_any(item, ("천식", "asthma", "copd")) for item in medical):
        workout_forbidden.update({"장시간 고강도 달리기"})
    if any(_contains_any(item, ("골다공증", "osteoporosis")) for item in medical):
        workout_forbidden.update({"점프", "무거운 척추 굴곡"})

    return {
        "allergies": allergies,
        "injuries": injuries,
        "medical_conditions": medical,
        "diet_type": diet_type,
        "goal": goal,
        "activity_level": activity_level,
        "available_time_minutes": available_time,
        "food_forbidden_terms": sorted(food_forbidden),
        "workout_forbidden_terms": sorted(workout_forbidden),
    }


def build_acsm_boundary(profile: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    activity = str(constraints.get("activity_level") or "").lower()
    goal = str(constraints.get("goal") or "").lower()
    minutes = int(constraints.get("available_time_minutes") or 30)
    if any(token in activity for token in ("초보", "beginner", "낮", "low")):
        rpe = [3, 5]
        sets = [1, 2]
    elif any(token in activity for token in ("상", "advanced", "높", "high")):
        rpe = [5, 7]
        sets = [2, 4]
    else:
        rpe = [4, 6]
        sets = [2, 3]

    if minutes <= 15:
        session = "minimal"
    elif minutes <= 30:
        session = "short"
    elif minutes <= 45:
        session = "standard"
    else:
        session = "extended"

    preferred = ["걷기", "가벼운 근력", "스트레칭"]
    if any(token in goal for token in ("감량", "체중", "weight")):
        preferred = ["걷기", "자전거", "전신 근력"]
    elif any(token in goal for token in ("근육", "증가", "muscle")):
        preferred = ["상체 근력", "하체 근력", "코어"]
    elif any(token in goal for token in ("유지", "건강", "health")):
        preferred = ["유산소", "근력", "스트레칭"]

    return {
        "rpe_range": rpe,
        "set_range": sets,
        "session_size": session,
        "allowed_categories": list(WORKOUT_CATEGORIES),
        "preferred_modalities": preferred,
        "forbidden_patterns": constraints.get("workout_forbidden_terms") or [],
    }


def build_diet_boundary(profile: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    goal = str(constraints.get("goal") or "").lower()
    if any(token in goal for token in ("감량", "체중", "weight")):
        calories = {"breakfast": 350, "lunch": 520, "dinner": 480}
    elif any(token in goal for token in ("근육", "증량", "muscle")):
        calories = {"breakfast": 480, "lunch": 680, "dinner": 650}
    else:
        calories = {"breakfast": 420, "lunch": 600, "dinner": 560}
    return {
        "meal_slots": list(MEAL_SLOTS),
        "slot_calories": calories,
        "forbidden_foods": constraints.get("food_forbidden_terms") or [],
        "diet_type": constraints.get("diet_type") or "",
    }


def _create_plan_items_for_action(state: GraphState, action: dict[str, Any]) -> list[dict[str, Any]]:
    domain = str(action.get("domain") or "")
    dates = _safe_str_list(_safe_dict(action.get("target")).get("dates")) or [_today().isoformat()]
    instruction = str(_safe_dict(action.get("request_detail")).get("instruction") or state.get("user_message") or "")
    if domain == "diet":
        return _build_diet_items(dates, _safe_dict(state.get("diet_boundary")), instruction)
    return _build_workout_items(dates, _safe_dict(state.get("acsm_boundary")), _safe_dict(state.get("profile_constraints")), instruction)


def _modify_plan_items_for_action(state: GraphState, action: dict[str, Any], base_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    domain = str(action.get("domain") or "")
    instruction = str(_safe_dict(action.get("request_detail")).get("instruction") or state.get("user_message") or "")
    target = _safe_dict(action.get("target"))
    items = deepcopy(base_items)
    if domain == "diet":
        return _modify_diet_items(items, _safe_dict(state.get("diet_boundary")), instruction, target)
    return _modify_workout_items(items, _safe_dict(state.get("acsm_boundary")), _safe_dict(state.get("profile_constraints")), instruction, target)


def _build_workout_items(
    dates: list[str],
    boundary: dict[str, Any],
    constraints: dict[str, Any],
    instruction: str,
) -> list[dict[str, Any]]:
    categories = _workout_pattern_for_request(dates, constraints, instruction)
    items: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        category = categories[index % len(categories)]
        exercises = _exercises_for_category(category, boundary, constraints)
        items.append(
            {
                "plan_type": "workout",
                "name": WORKOUT_CATEGORIES[category],
                "detail": ", ".join(ex["exercise_name"] for ex in exercises),
                "day": day,
                "ex_list": exercises,
            }
        )
    return items


def _workout_pattern_for_request(dates: list[str], constraints: dict[str, Any], instruction: str) -> list[str]:
    normalized = _normalize_text(instruction)
    if "스트레칭" in normalized and not any(token in normalized for token in ("상체", "하체", "유산소", "근력")):
        return ["stretching"]
    if "유산소" in normalized and "스트레칭" not in normalized:
        return ["cardio"]
    if "상체" in normalized:
        return ["upper", "stretching"] if len(dates) > 1 else ["upper"]
    if "하체" in normalized:
        return ["lower", "stretching"] if len(dates) > 1 else ["lower"]

    injury_text = " ".join(_safe_str_list(constraints.get("injuries")) + _safe_str_list(constraints.get("medical_conditions")))
    knee_sensitive = any(token in injury_text.lower() for token in ("무릎", "knee"))
    if knee_sensitive:
        return ["upper", "stretching", "cardio", "core", "upper", "stretching", "cardio"]
    goal = str(constraints.get("goal") or "").lower()
    if any(token in goal for token in ("근육", "증가", "muscle")):
        return ["upper", "lower", "stretching", "upper", "lower", "core", "cardio"]
    if any(token in goal for token in ("감량", "체중", "weight")):
        return ["cardio", "upper", "cardio", "lower", "stretching", "cardio", "core"]
    return ["upper", "cardio", "stretching", "lower", "full_body", "cardio", "stretching"]


def _exercises_for_category(category: str, boundary: dict[str, Any], constraints: dict[str, Any]) -> list[dict[str, Any]]:
    set_range = boundary.get("set_range") if isinstance(boundary.get("set_range"), list) else [2, 3]
    sets = int(set_range[0] if category == "stretching" else set_range[-1])
    library = {
        "cardio": [
            {"exercise_name": "빠른 걷기", "duration_minutes": 20, "calories": 90},
            {"exercise_name": "실내 자전거", "duration_minutes": 15, "calories": 80},
        ],
        "stretching": [
            {"exercise_name": "목/어깨 스트레칭", "sets": 1, "calories": 10},
            {"exercise_name": "햄스트링 스트레칭", "sets": 1, "calories": 10},
            {"exercise_name": "고양이 자세", "sets": 1, "calories": 10},
        ],
        "upper": [
            {"exercise_name": "무릎 대고 푸시업", "sets": sets, "calories": 35},
            {"exercise_name": "밴드 로우", "sets": sets, "calories": 35},
            {"exercise_name": "월 숄더 탭", "sets": sets, "calories": 25},
        ],
        "lower": [
            {"exercise_name": "의자 스쿼트", "sets": sets, "calories": 40},
            {"exercise_name": "글루트 브릿지", "sets": sets, "calories": 35},
            {"exercise_name": "스텝 백 런지", "sets": max(1, sets - 1), "calories": 35},
        ],
        "core": [
            {"exercise_name": "데드버그", "sets": sets, "calories": 30},
            {"exercise_name": "버드독", "sets": sets, "calories": 30},
            {"exercise_name": "사이드 플랭크", "sets": max(1, sets - 1), "calories": 25},
        ],
        "full_body": [
            {"exercise_name": "의자 스쿼트", "sets": sets, "calories": 35},
            {"exercise_name": "밴드 로우", "sets": sets, "calories": 35},
            {"exercise_name": "데드버그", "sets": sets, "calories": 25},
        ],
        "rest": [
            {"exercise_name": "가벼운 전신 스트레칭", "sets": 1, "calories": 15},
        ],
    }
    forbidden = _safe_str_list(constraints.get("workout_forbidden_terms"))
    return [_safe_exercise(exercise, forbidden) for exercise in library.get(category, library["full_body"])]


def _safe_exercise(exercise: dict[str, Any], forbidden: list[str]) -> dict[str, Any]:
    name = str(exercise.get("exercise_name") or "")
    if any(term and term in name for term in forbidden):
        return {"exercise_name": "관절 부담 낮은 대체 동작", "sets": 2, "calories": 20}
    return dict(exercise)


def _build_diet_items(dates: list[str], boundary: dict[str, Any], instruction: str) -> list[dict[str, Any]]:
    forbidden = _safe_str_list(boundary.get("forbidden_foods"))
    diet_type = str(boundary.get("diet_type") or "")
    calories = _safe_dict(boundary.get("slot_calories"))
    menus = _diet_menu_library(diet_type, forbidden, instruction)
    items: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        daily = menus[index % len(menus)]
        for slot in ("breakfast", "lunch", "dinner"):
            items.append(
                {
                    "plan_type": "diet",
                    "name": slot.capitalize(),
                    "detail": _clean_food_text(daily[slot], forbidden),
                    "day": day,
                    "calories": int(calories.get(slot) or 0),
                    "ex_list": [],
                }
            )
    return items


def _diet_menu_library(diet_type: str, forbidden: list[str], instruction: str) -> list[dict[str, str]]:
    normalized = _normalize_text(f"{diet_type} {instruction}")
    vegetarian = any(token in normalized for token in ("채식", "vegetarian", "비건", "vegan"))
    high_protein = any(token in normalized for token in ("근육", "단백질", "증량"))
    if vegetarian:
        base = [
            {"breakfast": "현미죽, 바나나", "lunch": "두부 스테이크, 현미밥, 구운 채소", "dinner": "렌틸콩 카레, 샐러드"},
            {"breakfast": "오트밀, 블루베리", "lunch": "병아리콩 샐러드, 고구마", "dinner": "두부구이, 버섯볶음, 보리밥"},
            {"breakfast": "잡곡죽, 사과", "lunch": "콩고기 채소볶음, 현미밥", "dinner": "채소 비빔밥, 된장국"},
        ]
    elif high_protein:
        base = [
            {"breakfast": "잡곡밥, 삶은 달걀, 토마토", "lunch": "닭가슴살 샐러드, 현미밥", "dinner": "연어구이, 브로콜리, 고구마"},
            {"breakfast": "현미죽, 닭가슴살 장조림", "lunch": "소고기 버섯볶음, 잡곡밥", "dinner": "두부부침, 채소볶음, 보리밥"},
            {"breakfast": "오트밀, 바나나", "lunch": "참치 채소덮밥", "dinner": "닭안심 오븐구이, 샐러드"},
        ]
    else:
        base = [
            {"breakfast": "현미죽, 블루베리, 삶은 달걀", "lunch": "현미밥, 두부 스테이크, 저염 구운 채소", "dinner": "두부 채소볶음, 고구마, 저염 데친 채소"},
            {"breakfast": "잡곡밥, 구운 고등어, 시금치나물", "lunch": "닭가슴살 샐러드, 현미밥", "dinner": "두부구이, 버섯볶음, 보리밥"},
            {"breakfast": "귀리밥, 바나나, 삶은 달걀", "lunch": "소고기 버섯볶음, 상추쌈", "dinner": "연어구이, 브로콜리, 고구마"},
            {"breakfast": "잡곡죽, 사과", "lunch": "닭안심 오븐구이, 토마토 샐러드", "dinner": "두부 된장국, 현미밥"},
        ]
    return [
        {slot: _clean_food_text(menu[slot], forbidden) for slot in ("breakfast", "lunch", "dinner")}
        for menu in base
    ]


def _clean_food_text(text: str, forbidden: list[str]) -> str:
    replacements = {
        "요거트": "두유",
        "우유": "두유",
        "치즈": "두부",
        "닭가슴살": "두부 스테이크",
        "닭안심": "두부",
        "소고기": "콩고기",
        "연어": "두부",
        "참치": "병아리콩",
        "고등어": "두부",
        "삶은 달걀": "두부구이",
        "달걀": "두부",
        "계란": "두부",
    }
    cleaned = str(text)
    for term in forbidden:
        if term and term in cleaned:
            cleaned = cleaned.replace(term, replacements.get(term, "두부"))
    return cleaned


def _modify_workout_items(
    items: list[dict[str, Any]],
    boundary: dict[str, Any],
    constraints: dict[str, Any],
    instruction: str,
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized = _normalize_text(instruction)
    next_items: list[dict[str, Any]] = []
    for item in items:
        copied = dict(item)
        copied["plan_type"] = "workout"
        should_change = _workout_item_matches_target(copied, target, normalized)
        if should_change:
            if "스트레칭" in normalized:
                category = "stretching"
            elif "유산소" in normalized:
                category = "cardio"
            elif "상체" in normalized:
                category = "upper"
            elif "하체" in normalized:
                category = "lower"
            elif "부담" in normalized or "무릎" in normalized or "가볍" in normalized:
                category = "stretching" if copied.get("name") == "하체" else _category_key_from_label(str(copied.get("name") or "")) or "stretching"
            else:
                category = _category_key_from_label(str(copied.get("name") or "")) or "full_body"
            exercises = _exercises_for_category(category, boundary, constraints)
            copied.update(
                {
                    "name": WORKOUT_CATEGORIES[category],
                    "detail": ", ".join(ex["exercise_name"] for ex in exercises),
                    "ex_list": exercises,
                }
            )
        copied["ex_list"] = [_safe_exercise(ex, _safe_str_list(constraints.get("workout_forbidden_terms"))) for ex in _safe_list(copied.get("ex_list"))]
        copied["detail"] = ", ".join(str(ex.get("exercise_name")) for ex in copied["ex_list"] if ex.get("exercise_name")) or str(copied.get("detail") or "")
        next_items.append(copied)
    return next_items


def _modify_diet_items(
    items: list[dict[str, Any]],
    boundary: dict[str, Any],
    instruction: str,
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized = _normalize_text(instruction)
    forbidden = _safe_str_list(boundary.get("forbidden_foods"))
    replacement = _diet_replacement_for_instruction(normalized, forbidden)
    next_items: list[dict[str, Any]] = []
    for item in items:
        copied = dict(item)
        copied["plan_type"] = "diet"
        if _diet_item_matches_target(copied, target, normalized):
            copied["detail"] = replacement
            if "가볍" in normalized or "간단" in normalized:
                copied["calories"] = min(int(copied.get("calories") or 400), 420)
        copied["detail"] = _clean_food_text(str(copied.get("detail") or ""), forbidden)
        copied["ex_list"] = []
        next_items.append(copied)
    return next_items


def _diet_replacement_for_instruction(normalized: str, forbidden: list[str]) -> str:
    if "현미죽" in normalized:
        return _clean_food_text("현미죽, 바나나", forbidden)
    if "두부" in normalized:
        return _clean_food_text("두부구이, 채소볶음, 보리밥", forbidden)
    if "고구마" in normalized:
        return _clean_food_text("고구마, 두부 샐러드", forbidden)
    if "단백질" in normalized:
        return _clean_food_text("닭가슴살 샐러드, 현미밥", forbidden)
    if "가볍" in normalized or "간단" in normalized:
        return _clean_food_text("현미죽, 사과", forbidden)
    return _clean_food_text("현미밥, 두부 스테이크, 구운 채소", forbidden)


def validate_plan(items: list[dict[str, Any]], contract: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    actions = [action for action in _safe_list(contract.get("actions")) if isinstance(action, dict)]
    expected_dates = sorted({date for action in actions for date in _safe_str_list(_safe_dict(action.get("target")).get("dates"))})
    if expected_dates:
        actual_dates = sorted({str(item.get("day") or "") for item in items if item.get("day")})
        if not set(expected_dates).issubset(actual_dates):
            issues.append({"severity": "critical", "code": "missing_target_dates", "message": "target dates missing"})

    for date_value in sorted({str(item.get("day") or "") for item in items if item.get("day")}):
        diet_slots = {
            _slot_key(str(item.get("name") or ""))
            for item in items
            if item.get("plan_type") == "diet" and item.get("day") == date_value
        }
        if diet_slots and diet_slots != set(MEAL_SLOTS):
            issues.append({"severity": "critical", "code": "missing_meal_slots", "message": f"{date_value} meal slots incomplete"})

    forbidden_foods = _safe_str_list(constraints.get("food_forbidden_terms"))
    forbidden_workouts = _safe_str_list(constraints.get("workout_forbidden_terms"))
    for item in items:
        text = f"{item.get('name', '')} {item.get('detail', '')} {item.get('ex_list', '')}"
        if item.get("plan_type") == "diet" and any(term and term in text for term in forbidden_foods):
            issues.append({"severity": "critical", "code": "forbidden_food", "message": str(item)})
        if item.get("plan_type") == "workout" and any(term and term in text for term in forbidden_workouts):
            issues.append({"severity": "critical", "code": "forbidden_workout", "message": str(item)})
        if item.get("plan_type") == "workout" and item.get("name") == "유산소" and "스트레칭" in text and "걷기" not in text:
            issues.append({"severity": "critical", "code": "stretching_as_cardio", "message": str(item)})

    return {"passed": not any(issue["severity"] == "critical" for issue in issues), "issues": issues}


def repair_plan(items: list[dict[str, Any]], constraints: dict[str, Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        copied = dict(item)
        if copied.get("plan_type") == "diet":
            copied["detail"] = _clean_food_text(str(copied.get("detail") or ""), _safe_str_list(constraints.get("food_forbidden_terms")))
        elif copied.get("plan_type") == "workout":
            category = _category_key_from_label(str(copied.get("name") or "")) or "stretching"
            copied["ex_list"] = [_safe_exercise(ex, _safe_str_list(constraints.get("workout_forbidden_terms"))) for ex in _safe_list(copied.get("ex_list"))]
            copied["name"] = WORKOUT_CATEGORIES[category]
            copied["detail"] = ", ".join(str(ex.get("exercise_name")) for ex in copied["ex_list"] if ex.get("exercise_name"))
        repaired.append(copied)
    return repaired


def _build_plan_preview_response(
    *,
    state: GraphState,
    persona_id: str,
    items: list[dict[str, Any]],
    proposed_type: str,
    write_mode: str,
    commit_now: bool,
) -> str:
    spec = PERSONA_SPECS[_resolve_persona_id({"selected_ai_persona": persona_id})]
    type_label = "운동/식단" if proposed_type == "bundle" else "식단" if proposed_type == "diet" else "운동"
    dates = sorted({str(item.get("day") or "") for item in items if item.get("day")})
    scope_label = "일주일" if len(dates) >= 7 else "오늘" if len(dates) == 1 else f"{len(dates)}일"
    verb = "수정했어" if write_mode == "update" and spec["speech"] == "casual" else "수정했습니다" if write_mode == "update" else "맞춰봤어" if spec["speech"] == "casual" else "맞췄습니다"
    if commit_now:
        verb = "수정해서 반영할게" if spec["speech"] == "casual" else "수정해서 반영하겠습니다"

    lines = [f"{spec['intro']} {scope_label} {type_label} 플랜을 {verb}."]
    if any(item.get("plan_type") == "workout" for item in items):
        lines.extend(_preview_workout_lines(items))
    if any(item.get("plan_type") == "diet" for item in items):
        if any(item.get("plan_type") == "workout" for item in items):
            lines.append("")
        lines.extend(_preview_diet_lines(items))
    if not commit_now:
        lines.append(str(spec["plan_tail"]))
    return "\n".join(line for line in lines if line is not None).strip()


def _preview_workout_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in sorted([i for i in items if i.get("plan_type") == "workout"], key=lambda i: str(i.get("day") or ""))[:7]:
        day = str(item.get("day") or "")
        detail = _short_detail(str(item.get("detail") or ""), max_parts=3)
        lines.append(f"- {_date_label(day)}: {item.get('name')} - {detail}")
    return lines


def _preview_diet_lines(items: list[dict[str, Any]]) -> list[str]:
    by_date: dict[str, dict[str, str]] = {}
    for item in items:
        if item.get("plan_type") != "diet":
            continue
        day = str(item.get("day") or "")
        slot = _slot_key(str(item.get("name") or ""))
        by_date.setdefault(day, {})[slot] = str(item.get("detail") or "")
    lines: list[str] = []
    for day in sorted(by_date)[:7]:
        slots = by_date[day]
        lines.append(
            f"- {_date_label(day)}: 아침 {slots.get('breakfast', '-')}"
            f" / 점심 {slots.get('lunch', '-')}"
            f" / 저녁 {slots.get('dinner', '-')}"
        )
    return lines


def _build_non_plan_response(state: GraphState, contract: dict[str, Any], persona_id: str) -> str:
    route_kind = str(contract.get("route_kind") or "")
    message = str(state.get("user_message") or "")
    spec = PERSONA_SPECS[_resolve_persona_id({"selected_ai_persona": persona_id})]
    if route_kind == "safety":
        return "지금은 운동이나 식단보다 안전 확인이 먼저예요. 가슴 통증, 실신, 호흡곤란, 자해 위험이면 즉시 119나 가까운 응급실에 연락해 주세요."
    if route_kind == "care":
        return "괜찮아, 오늘은 부담을 낮추는 쪽으로 가자. 5분 산책이나 물 한 컵처럼 바로 가능한 것 하나만 잡아도 충분해." if spec["speech"] == "casual" else "괜찮습니다. 오늘은 부담을 낮추는 쪽이 좋겠습니다. 5분 산책이나 물 한 컵처럼 바로 가능한 것 하나만 잡아도 충분합니다."
    if "소개" in message or "누구" in message:
        if persona_id == "strict_trainer":
            return "나는 FitUs 직진 PT쌤이야. 운동/식단 플랜을 짧게 잡고, 수정하고, 승인하면 캘린더에 바로 반영해."
        if persona_id == "daily_manager":
            return "저는 FitUs 생활 매니저입니다. 운동/식단 플랜 작성, 수정, 승인, 캘린더 반영 범위를 정리해서 처리합니다."
        if persona_id == "playful_buddy":
            return "나는 FitUs 운동 메이트야. 운동이든 식단이든 너무 무겁지 않게 같이 맞춰보는 역할이야."
        if persona_id == "kind_younger":
            return "저는 FitUs 다정 동생이에요. 운동과 식단을 무리 없게 같이 정리하고, 괜찮은 흐름으로 챙겨드릴게요."
        if persona_id == "routine_master":
            return "저는 FitUs 루틴 장인입니다. 지속 가능한 운동/식단 루틴으로 반복하기 쉽게 정리합니다."
        return "나는 FitUs 응원 누나야. 운동이랑 식단 플랜을 밝게 맞춰주고, 네가 승인하면 캘린더에 반영해."
    if route_kind == "info":
        active = _safe_dict(state.get("active_proposal"))
        if active.get("items"):
            return "지금 제안한 플랜 기준으로는 큰 충돌은 없어. 마음에 안 드는 날짜나 끼니만 말해주면 그 부분만 바꿔줄게." if spec["speech"] == "casual" else "현재 제안한 플랜 기준으로 큰 충돌은 없습니다. 마음에 들지 않는 날짜나 끼니만 말씀해 주시면 해당 부분만 조정하겠습니다."
        return "운동이나 식단 기준을 물어보면 바로 짧게 정리해줄게. 플랜이 필요하면 날짜와 종류를 같이 말해줘." if spec["speech"] == "casual" else "운동이나 식단 기준을 물어보시면 짧게 정리하겠습니다. 플랜이 필요하면 날짜와 종류를 함께 말씀해 주세요."
    return "좋아, 운동 플랜이나 식단 플랜이 필요하면 바로 말해줘." if spec["speech"] == "casual" else "좋습니다. 운동 플랜이나 식단 플랜이 필요하면 바로 말씀해 주세요."


def _build_delete_response(action: dict[str, Any], persona_id: str) -> str:
    spec = PERSONA_SPECS[_resolve_persona_id({"selected_ai_persona": persona_id})]
    domain = str(action.get("domain") or "all")
    target = _safe_dict(action.get("target"))
    scope = str(target.get("scope") or "")
    plan_label = "운동/식단" if domain == "all" else "식단" if domain == "diet" else "운동"
    if scope == "all":
        return f"{spec['delete_done']} 현재 캘린더의 모든 {plan_label} 내역을 대상으로 처리할게." if spec["speech"] == "casual" else f"{spec['delete_done']} 현재 캘린더의 모든 {plan_label} 내역을 대상으로 처리합니다."
    dates = _safe_str_list(target.get("dates"))
    date_label = f"{dates[0]}" if len(dates) == 1 else f"{dates[0]}부터 {dates[-1]}까지" if dates else "요청한 날짜"
    return f"{spec['delete_done']} {date_label} {plan_label} 내역을 대상으로 처리할게." if spec["speech"] == "casual" else f"{spec['delete_done']} {date_label} {plan_label} 내역을 대상으로 처리합니다."


def _persona_done(persona_id: str, *, domain: str) -> str:
    del domain
    return str(PERSONA_SPECS[_resolve_persona_id({"selected_ai_persona": persona_id})]["done"])


def _persona_clarify(persona_id: str, question: str) -> str:
    spec = PERSONA_SPECS[_resolve_persona_id({"selected_ai_persona": persona_id})]
    if spec["speech"] == "casual":
        return question
    return question.replace("알려줘", "알려주세요").replace("말해줘", "말씀해 주세요")


def _generation_updates(
    state: GraphState,
    *,
    response: str,
    persona_id: str,
    intent: str | None = None,
    action_intent: str | None = None,
    proposed_plan: list[dict[str, Any]] | None = None,
    proposed_plan_type: str | None = None,
    proposed_plan_action: str | None = None,
    awaiting_plan_confirmation: bool | None = None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "response": response,
        "draft_response": response,
        "resolved_persona_id": persona_id,
        "self_eval_count": 0,
        "self_eval_failure_reason": None,
        "force_regenerate": False,
    }
    if intent is not None:
        updates["intent"] = intent
    if action_intent is not None:
        updates["action_intent"] = action_intent
    if proposed_plan is not None:
        updates["proposed_plan"] = proposed_plan
    if proposed_plan_type is not None:
        updates["proposed_plan_type"] = proposed_plan_type
    if proposed_plan_action is not None:
        updates["proposed_plan_action"] = proposed_plan_action
    if awaiting_plan_confirmation is not None:
        updates["awaiting_plan_confirmation"] = awaiting_plan_confirmation
    if proposed_plan:
        updates["draft_components"] = {
            "core_message": response.splitlines()[0] if response else "",
            "reason_points": [],
            "suggested_action": "",
            "plan_preview": "\n".join(response.splitlines()[1:]),
            "safety_notes": [],
            "approval_question": response.splitlines()[-1] if response else None,
            "search_grounding_summary": "",
        }
    else:
        updates["draft_components"] = None
    return updates


async def _load_saved_planner_slice(
    deps: NodeDeps,
    state: GraphState,
    *,
    domain: str,
    dates: list[str],
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        if domain == "diet":
            payload = await deps.was.get_diet_plan_full(str(state.get("user_id") or ""))
        else:
            payload = await deps.was.get_workout_plan_full(str(state.get("user_id") or ""))
    except Exception:
        return []
    return _slice_plan_items(_safe_dict(payload).get("items"), domain=domain, dates=dates, target=target)


def _slice_plan_items(items: object, *, domain: str, dates: list[str], target: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    date_set = set(dates)
    meal_slots = set(_safe_str_list(target.get("meal_slots")))
    workout_categories = set(_safe_str_list(target.get("workout_categories")))
    for item in _safe_plan_items(items):
        item_domain = _infer_item_domain(item)
        if domain in PLAN_DOMAINS and item_domain != domain:
            continue
        if date_set and str(item.get("day") or "") not in date_set:
            continue
        if meal_slots and item_domain == "diet" and _slot_key(str(item.get("name") or "")) not in meal_slots:
            continue
        if workout_categories and item_domain == "workout" and (_category_key_from_label(str(item.get("name") or "")) not in workout_categories):
            continue
        next_item = dict(item)
        next_item.setdefault("plan_type", item_domain)
        selected.append(next_item)
    return selected


def _resource_slice_target(operation: str, target: dict[str, Any]) -> dict[str, Any]:
    if operation != "plan.modify":
        return target
    return {
        **target,
        "meal_slots": [],
        "workout_categories": [],
        "food_keywords": [],
        "exercise_keywords": [],
    }


def _delete_payload_from_action(action: dict[str, Any]) -> dict[str, Any]:
    target = _safe_dict(action.get("target"))
    domain = str(action.get("domain") or "all")
    return {
        "plan_type": domain if domain in {"workout", "diet"} else "all",
        "target_scope": str(target.get("scope") or "dates"),
        "target_dates": [] if str(target.get("scope")) == "all" else _safe_str_list(target.get("dates")),
    }


def _looks_like_delete(normalized: str) -> bool:
    return _has_any(normalized, DELETE_KEYWORDS) and (
        _has_any(normalized, WORKOUT_KEYWORDS + DIET_KEYWORDS) or "캘린더" in normalized or "플랜" in normalized or "내역" in normalized
    )


def _looks_like_modify(normalized: str) -> bool:
    return _has_any(normalized, MODIFY_KEYWORDS)


def _can_route_plan_modify_without_clarification(
    normalized: str,
    active: dict[str, Any],
    domains: list[str],
) -> bool:
    if active:
        return True
    if any(token in normalized for token in ("캘린더", "플래너", "플랜", "계획", "루틴", "내역")):
        return True
    if not domains:
        return False
    hints = _target_hints(normalized, [])
    if domains == ["diet"] and hints.get("meal_slots"):
        return True
    if domains == ["workout"] and hints.get("workout_categories"):
        return True
    return _has_explicit_date_signal(normalized)


def _looks_like_approval(normalized: str) -> bool:
    return _has_any(normalized, APPROVAL_KEYWORDS)


def _detect_domains(normalized: str) -> list[str]:
    has_workout = _has_any(normalized, WORKOUT_KEYWORDS)
    has_diet = _has_any(normalized, DIET_KEYWORDS)
    if any(token in normalized for token in ("둘 다", "둘다", "운동/식단", "식단/운동", "운동이랑 식단", "식단이랑 운동")):
        return ["workout", "diet"]
    domains = []
    if has_workout:
        domains.append("workout")
    if has_diet:
        domains.append("diet")
    return domains


def _target_hints(normalized: str, dates: list[str]) -> dict[str, Any]:
    meal_slots: list[str] = []
    if "아침" in normalized or "breakfast" in normalized:
        meal_slots.append("breakfast")
    if "점심" in normalized or "lunch" in normalized:
        meal_slots.append("lunch")
    if "저녁" in normalized or "dinner" in normalized:
        meal_slots.append("dinner")

    workout_categories: list[str] = []
    for key, label in WORKOUT_CATEGORIES.items():
        if label in normalized or key in normalized:
            workout_categories.append(key)

    return {
        "dates": dates,
        "meal_slots": meal_slots,
        "workout_categories": workout_categories,
        "food_keywords": [],
        "exercise_keywords": [],
    }


def _extract_dates(message: str) -> list[str]:
    today = _today()
    normalized = _normalize_text(message)
    explicit = _extract_explicit_dates(message)
    if explicit:
        return explicit
    weekday = _extract_weekday(normalized)
    if "다음주" in normalized or "다음 주" in normalized:
        next_monday = today + timedelta(days=(7 - today.weekday()))
        if weekday is not None:
            return [(next_monday + timedelta(days=weekday)).isoformat()]
        return [(next_monday + timedelta(days=offset)).isoformat() for offset in range(7)]
    if "이번주" in normalized or "이번 주" in normalized:
        this_monday = today - timedelta(days=today.weekday())
        if weekday is not None:
            return [(this_monday + timedelta(days=weekday)).isoformat()]
        return [(today + timedelta(days=offset)).isoformat() for offset in range(7)]
    if any(token in normalized for token in ("일주일", "1주일", "7일", "한 주")):
        return [(today + timedelta(days=offset)).isoformat() for offset in range(7)]
    if "내일" in normalized:
        return [(today + timedelta(days=1)).isoformat()]
    if "모레" in normalized:
        return [(today + timedelta(days=2)).isoformat()]
    if weekday is not None:
        delta = (weekday - today.weekday()) % 7
        return [(today + timedelta(days=delta)).isoformat()]
    return [today.isoformat()]


def _resolve_modify_dates(message: str, active: dict[str, Any], domain: str, fallback_dates: list[str]) -> list[str]:
    if active and not _has_explicit_date_signal(message):
        dates = _dates_from_items(active.get("items"), domain=domain)
        if dates:
            return dates
    return fallback_dates


def _has_explicit_date_signal(message: str) -> bool:
    normalized = _normalize_text(message)
    if _extract_explicit_dates(message):
        return True
    return any(
        token in normalized
        for token in (
            "오늘",
            "내일",
            "모레",
            "월요일",
            "화요일",
            "수요일",
            "목요일",
            "금요일",
            "토요일",
            "일요일",
            "일주일",
            "1주일",
            "7일",
            "이번주",
            "이번 주",
            "다음주",
            "다음 주",
        )
    )


def _extract_explicit_dates(message: str) -> list[str]:
    dates: list[str] = []
    for match in re.finditer(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", message):
        year, month, day = map(int, match.groups())
        try:
            dates.append(date(year, month, day).isoformat())
        except ValueError:
            continue
    for match in re.finditer(r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일", message):
        month, day = map(int, match.groups())
        try:
            dates.append(date(_today().year, month, day).isoformat())
        except ValueError:
            continue
    return sorted(dict.fromkeys(dates))


def _extract_weekday(normalized: str) -> int | None:
    weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
    for label, value in weekdays.items():
        if label in normalized:
            return value
    return None


def _today() -> date:
    return datetime.now(KST).date()


def _effective_profile(state: GraphState) -> dict[str, Any]:
    return _safe_dict(state.get("effective_user_profile")) or _safe_dict(state.get("user_profile"))


def _resolve_persona_id(profile: dict[str, Any]) -> str:
    raw = str(profile.get("selected_ai_persona") or "cheer_sis").strip() or "cheer_sis"
    mapped = PERSONA_ALIASES.get(raw, raw)
    return mapped if mapped in PERSONA_SPECS else "cheer_sis"


def _profile_list(profile: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = profile.get(key)
        if isinstance(raw, str):
            pieces = re.split(r"[,/|;]", raw)
            values.extend(piece.strip() for piece in pieces if piece.strip())
        elif isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return sorted(dict.fromkeys(values))


def _profile_text(profile: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = profile.get(key)
        if value not in (None, "", [], {}):
            return " ".join(str(value).split())
    return ""


def _profile_int(profile: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        try:
            value = int(profile.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return default


def _contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    text = str(value).lower()
    return any(keyword.lower() in text for keyword in keywords)


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_str_list(value: object) -> list[str]:
    return [str(item).strip() for item in _safe_list(value) if str(item).strip()]


def _safe_plan_items(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in _safe_list(value) if isinstance(item, dict)]


def _safe_domain(value: str) -> str:
    return value if value in {"workout", "diet", "all", "bundle", "profile", "general"} else "general"


def _first_action(contract: dict[str, Any]) -> dict[str, Any] | None:
    for action in _safe_list(contract.get("actions")):
        if isinstance(action, dict):
            return action
    return None


def _domains_from_items(items: object) -> list[str]:
    domains = []
    for item in _safe_plan_items(items):
        domain = _infer_item_domain(item)
        if domain in PLAN_DOMAINS and domain not in domains:
            domains.append(domain)
    return domains or ["workout"]


def _dates_from_items(items: object, *, domain: str) -> list[str]:
    dates = sorted(
        {
            str(item.get("day") or "")
            for item in _safe_plan_items(items)
            if str(item.get("day") or "") and (domain == "bundle" or _infer_item_domain(item) == domain)
        }
    )
    return dates


def _infer_item_domain(item: dict[str, Any]) -> str:
    plan_type = str(item.get("plan_type") or item.get("type") or "").lower()
    if plan_type in PLAN_DOMAINS:
        return plan_type
    if item.get("ex_list"):
        return "workout"
    name = str(item.get("name") or "").lower()
    if _slot_key(name) in MEAL_SLOTS:
        return "diet"
    return "diet" if any(token in name for token in ("breakfast", "lunch", "dinner", "아침", "점심", "저녁")) else "workout"


def _proposal_domain_from_items(items: list[dict[str, Any]], fallback: str) -> str:
    domains = {_infer_item_domain(item) for item in items}
    domains.discard("")
    if len(domains) > 1:
        return "bundle"
    if domains:
        return next(iter(domains))
    return fallback if fallback in {"workout", "diet", "bundle"} else "workout"


def _dedupe_plan_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = (
            str(item.get("plan_type") or ""),
            str(item.get("day") or ""),
            str(item.get("name") or ""),
            str(item.get("detail") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _slot_key(value: str) -> str:
    text = str(value or "").lower()
    if "breakfast" in text or "아침" in text:
        return "breakfast"
    if "lunch" in text or "점심" in text:
        return "lunch"
    if "dinner" in text or "저녁" in text:
        return "dinner"
    return text


def _category_key_from_label(value: str) -> str | None:
    text = str(value or "").lower()
    for key, label in WORKOUT_CATEGORIES.items():
        if key in text or label in text:
            return key
    return None


def _workout_item_matches_target(item: dict[str, Any], target: dict[str, Any], normalized: str) -> bool:
    categories = set(_safe_str_list(target.get("workout_categories")))
    if not categories:
        return True
    current = _category_key_from_label(str(item.get("name") or ""))
    if current in categories:
        return True
    return any(WORKOUT_CATEGORIES.get(category, category) in normalized for category in categories)


def _diet_item_matches_target(item: dict[str, Any], target: dict[str, Any], normalized: str) -> bool:
    del normalized
    slots = set(_safe_str_list(target.get("meal_slots")))
    if not slots:
        return True
    current = _slot_key(str(item.get("name") or ""))
    return current in slots


def _date_label(day: str) -> str:
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return day
    weekdays = "월화수목금토일"
    return f"{parsed.month}/{parsed.day} {weekdays[parsed.weekday()]}"


def _short_detail(detail: str, *, max_parts: int) -> str:
    parts = [part.strip() for part in re.split(r"[,/]", detail) if part.strip()]
    return ", ".join(parts[:max_parts]) if parts else detail


def _trim_response(response: str) -> str:
    lines = [line.rstrip() for line in str(response or "").strip().splitlines()]
    return "\n".join(lines).strip()
