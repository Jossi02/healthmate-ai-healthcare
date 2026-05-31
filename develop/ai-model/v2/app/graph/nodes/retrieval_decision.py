"""Decide whether retrieval is needed before entering the search node."""
from __future__ import annotations

import time

from app.core.conversation_state import infer_domain
from app.core.intents import INTENT_CARE, INTENT_INFO, INTENT_MODIFY, INTENT_PLAN
from app.core.profile_constraints import (
    query_mentions_specialized_topic,
    query_needs_evidence,
    query_needs_user_memory,
)
from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

_INFO_WEB_KEYWORDS = ("최신", "최근", "요즘", "뉴스", "업데이트")
_VALID_TARGETS = {"vdb_memory", "vdb_user_important", "vdb_external", "web"}


def make_retrieval_decision_node(deps: NodeDeps):
    async def retrieval_decision_node(state: GraphState) -> dict:
        started_at = time.perf_counter()
        query = _resolved_query(state)
        initial_targets = [target for target in (state.get("search_targets") or []) if target in _VALID_TARGETS]
        decision = _build_decision(state, query, initial_targets)

        deps.trace.record_current_event(
            stage="retrieval_decision",
            status="ok" if decision["should_search"] else "info",
            title="Retrieval decision prepared",
            detail={
                "should_search": decision["should_search"],
                "reason": decision["reason"],
                "targets": decision["targets"],
                "domain": decision["domain"],
                "requires_external": decision["requires_external"],
                "requires_memory": decision["requires_memory"],
                "requires_web": decision["requires_web"],
            },
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return {
            "retrieval_decision": decision,
            "search_targets": decision["targets"],
            "search_query": query,
        }

    return retrieval_decision_node


def _build_decision(state: GraphState, query: str, initial_targets: list[str]) -> dict:
    intent = state.get("intent", "")
    action_intent = str(state.get("action_intent") or "")
    domain = _resolved_domain(state, query)
    profile_constraints = state.get("profile_constraints") or {}
    targets = list(dict.fromkeys(initial_targets))

    requires_external = False
    requires_memory = False
    requires_web = False
    reason_parts: list[str] = []

    if state.get("request_kind") == "home_recommendation":
        return _decision(False, [], domain, "home_recommendation", False, False, False)

    if intent in {INTENT_PLAN, INTENT_MODIFY} or action_intent in {"create", "modify"}:
        requires_external = bool(profile_constraints.get("should_use_rag")) or query_needs_evidence(query) or query_mentions_specialized_topic(query)
        requires_memory = query_needs_user_memory(query)
        targets = [target for target in targets if target != "web"]
        targets = _without_memory(targets) if not requires_memory else _with_memory(targets)
        if requires_external:
            targets = _append_once(targets, "vdb_external")
            reason_parts.append("profile_or_specialized_plan")
        else:
            targets = [target for target in targets if target != "vdb_external"]
            reason_parts.append("low_risk_plan")
        if requires_memory:
            reason_parts.append("user_memory_reference")
        return _decision(bool(targets), targets, domain, "+".join(reason_parts), requires_external, requires_memory, False)

    if intent == INTENT_INFO or action_intent == "info":
        requires_external = True
        requires_memory = query_needs_user_memory(query)
        requires_web = _needs_web(query)
        targets = _append_once(targets, "vdb_external")
        targets = _with_memory(targets) if requires_memory else _without_memory(targets)
        if requires_web:
            targets = _append_once(targets, "web")
        else:
            targets = [target for target in targets if target != "web"]
        return _decision(True, targets, domain, "info_answer", requires_external, requires_memory, requires_web)

    if intent == INTENT_CARE and state.get("requires_past_memory"):
        targets = _with_memory(targets)
        return _decision(True, targets, domain, "care_memory", False, True, False)

    if query_needs_user_memory(query):
        targets = _with_memory(targets)
        return _decision(True, targets, domain, "explicit_memory_reference", False, True, False)

    return _decision(False, [], domain, "no_retrieval_needed", False, False, False)


def _decision(
    should_search: bool,
    targets: list[str],
    domain: str,
    reason: str,
    requires_external: bool,
    requires_memory: bool,
    requires_web: bool,
) -> dict:
    return {
        "should_search": should_search,
        "targets": list(dict.fromkeys(targets)),
        "domain": domain,
        "reason": reason,
        "requires_external": requires_external,
        "requires_memory": requires_memory,
        "requires_web": requires_web,
    }


def _resolved_query(state: GraphState) -> str:
    resolution = state.get("context_resolution") or {}
    resolved_text = str(resolution.get("resolved_text") or "").strip()
    resolved_reference = resolution.get("resolved_reference")
    confidence = float(resolution.get("confidence") or 0.0)
    if resolved_reference and resolved_reference != "none" and resolved_text and confidence >= 0.6:
        return resolved_text
    return str(state.get("user_message") or "")


def _resolved_domain(state: GraphState, query: str) -> str:
    for value in (
        state.get("modify_target"),
        state.get("domain"),
        (state.get("context_resolution") or {}).get("resolved_domain"),
        infer_domain(query),
    ):
        if value in {"workout", "diet", "profile", "general"}:
            return str(value)
    return "general"


def _needs_web(query: str) -> bool:
    normalized = str(query or "").lower()
    return any(keyword in normalized for keyword in _INFO_WEB_KEYWORDS)


def _append_once(targets: list[str], target: str) -> list[str]:
    if target not in targets:
        targets.append(target)
    return targets


def _with_memory(targets: list[str]) -> list[str]:
    for target in ("vdb_memory", "vdb_user_important"):
        targets = _append_once(targets, target)
    return targets


def _without_memory(targets: list[str]) -> list[str]:
    return [target for target in targets if target not in {"vdb_memory", "vdb_user_important"}]
