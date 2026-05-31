"""LangGraph builder for the v2 main flow."""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.core.intents import (
    INTENT_APPROVAL,
    INTENT_CARE,
    INTENT_CASUAL,
    INTENT_FALLBACK,
    INTENT_HOME_RECOMMENDATION,
    INTENT_INFO,
    INTENT_MODIFY,
    INTENT_PLAN,
    INTENT_RECORD,
    INTENT_SAFETY,
    normalize_intent,
)
from app.graph.deps import NodeDeps
from app.graph.nodes.care import make_care_node
from app.graph.nodes.context_resolver import make_context_resolver_node
from app.graph.nodes.answer_validator import make_answer_validator_node
from app.graph.nodes.fallback import make_fallback_node
from app.graph.nodes.finalize import make_finalize_node
from app.graph.nodes.generate import make_generate_node
from app.graph.nodes.intent import make_intent_node
from app.graph.nodes.modify import make_modify_node
from app.graph.nodes.preprocess import make_preprocess_node
from app.graph.nodes.profile_constraints import make_profile_constraints_node
from app.graph.nodes.record import make_record_node
from app.graph.nodes.retrieval_decision import make_retrieval_decision_node
from app.graph.nodes.safety import make_safety_node
from app.graph.nodes.search import make_search_node
from app.schemas.state import GraphState


def route_intent(state: GraphState) -> str:
    intent = normalize_intent(state.get("intent", INTENT_FALLBACK))
    mapping = {
        INTENT_CASUAL: "generate",
        INTENT_SAFETY: "safety",
        INTENT_FALLBACK: "fallback",
        INTENT_CARE: "care",
        INTENT_RECORD: "record",
        INTENT_PLAN: "retrieval_decision",
        INTENT_MODIFY: "modify_load",
        INTENT_INFO: "retrieval_decision",
        INTENT_APPROVAL: "generate",
        INTENT_HOME_RECOMMENDATION: "generate",
    }
    return mapping.get(intent, "fallback")


def route_care(state: GraphState) -> str:
    if state.get("requires_past_memory", False):
        return "retrieval_decision"
    return "generate"


def route_retrieval_decision(state: GraphState) -> str:
    decision = state.get("retrieval_decision") or {}
    targets = decision.get("targets") or state.get("search_targets") or []
    if decision.get("should_search") and targets:
        return "search"
    return "generate"


def route_search_retry(state: GraphState) -> str:
    quality = state.get("search_quality", "ok")
    results = state.get("search_results") or []
    retry = state.get("search_retry_count", 0)

    if quality == "degraded":
        return "generate"
    if results:
        return "generate"
    if retry > 0:
        return "search"
    return "generate"


def route_generate_self_eval(state: GraphState):
    if state.get("request_kind") == "home_recommendation":
        return "finalize"
    if state.get("self_eval_failure_reason"):
        return "generate"
    if state.get("response"):
        return "answer_validator"
    return "finalize"


def route_answer_validation(state: GraphState) -> str:
    report = state.get("validation_report") or {}
    if report.get("requires_retry") and state.get("self_eval_failure_reason"):
        return "generate"
    return "finalize"


def build_graph(deps: NodeDeps, checkpointer: BaseCheckpointSaver):
    """Build and compile the LangGraph state machine."""
    builder = StateGraph(GraphState)

    builder.add_node("preprocess", make_preprocess_node(deps))
    builder.add_node("context_resolver", make_context_resolver_node(deps))
    builder.add_node("analyze_intent", make_intent_node(deps))
    builder.add_node("profile_constraints", make_profile_constraints_node(deps))
    builder.add_node("retrieval_decision", make_retrieval_decision_node(deps))
    builder.add_node("safety", make_safety_node(deps))
    builder.add_node("care", make_care_node(deps))
    builder.add_node("record", make_record_node(deps))
    builder.add_node("search", make_search_node(deps))
    builder.add_node("modify_load", make_modify_node(deps))
    builder.add_node("fallback", make_fallback_node(deps))
    builder.add_node("generate", make_generate_node(deps))
    builder.add_node("answer_validator", make_answer_validator_node(deps))
    builder.add_node("finalize", make_finalize_node(deps))

    builder.add_edge(START, "preprocess")
    builder.add_edge("preprocess", "context_resolver")
    builder.add_edge("context_resolver", "analyze_intent")
    builder.add_edge("analyze_intent", "profile_constraints")

    builder.add_conditional_edges(
        "profile_constraints",
        route_intent,
        {
            "generate": "generate",
            "safety": "safety",
            "fallback": "fallback",
            "care": "care",
            "record": "record",
            "retrieval_decision": "retrieval_decision",
            "modify_load": "modify_load",
        },
    )

    builder.add_conditional_edges(
        "care",
        route_care,
        {"retrieval_decision": "retrieval_decision", "generate": "generate"},
    )

    builder.add_edge("record", "generate")
    builder.add_edge("modify_load", "retrieval_decision")

    builder.add_conditional_edges(
        "retrieval_decision",
        route_retrieval_decision,
        {"search": "search", "generate": "generate"},
    )

    builder.add_conditional_edges(
        "search",
        route_search_retry,
        {"search": "search", "generate": "generate"},
    )

    builder.add_edge("fallback", "finalize")

    builder.add_conditional_edges(
        "generate",
        route_generate_self_eval,
        {"generate": "generate", "answer_validator": "answer_validator", "finalize": "finalize", END: END},
    )

    builder.add_conditional_edges(
        "answer_validator",
        route_answer_validation,
        {"generate": "generate", "finalize": "finalize"},
    )

    builder.add_edge("safety", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
