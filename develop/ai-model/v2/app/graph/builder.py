"""LangGraph builder for the fast v2 chat flow."""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.deps import NodeDeps
from app.graph.nodes.fast_flow import (
    make_fast_finalize_node,
    make_fast_generate_node,
    make_fast_profile_constraints_node,
    make_fast_router_node,
    make_fast_target_resource_node,
    make_fast_validate_node,
)
from app.graph.nodes.generate import make_generate_node
from app.graph.nodes.preprocess import make_preprocess_node
from app.schemas.state import GraphState


def route_after_preprocess(state: GraphState) -> str:
    if state.get("request_kind") == "home_recommendation":
        return "legacy_home_generate"
    return "fast_router"


def build_graph(deps: NodeDeps, checkpointer: BaseCheckpointSaver):
    """Build and compile the LangGraph state machine.

    Chat requests use the new fast plan flow. The home recommendation endpoint
    keeps the legacy generator because it already returns the HomeRecommendation
    response contract used by the frontend.
    """
    builder = StateGraph(GraphState)

    builder.add_node("preprocess", make_preprocess_node(deps))
    builder.add_node("legacy_home_generate", make_generate_node(deps))
    builder.add_node("fast_router", make_fast_router_node(deps))
    builder.add_node("fast_target_resource", make_fast_target_resource_node(deps))
    builder.add_node("fast_profile_constraints", make_fast_profile_constraints_node(deps))
    builder.add_node("fast_generate", make_fast_generate_node(deps))
    builder.add_node("fast_validate", make_fast_validate_node(deps))
    builder.add_node("fast_finalize", make_fast_finalize_node(deps))

    builder.add_edge(START, "preprocess")
    builder.add_conditional_edges(
        "preprocess",
        route_after_preprocess,
        {"legacy_home_generate": "legacy_home_generate", "fast_router": "fast_router"},
    )
    builder.add_edge("legacy_home_generate", END)
    builder.add_edge("fast_router", "fast_target_resource")
    builder.add_edge("fast_target_resource", "fast_profile_constraints")
    builder.add_edge("fast_profile_constraints", "fast_generate")
    builder.add_edge("fast_generate", "fast_validate")
    builder.add_edge("fast_validate", "fast_finalize")
    builder.add_edge("fast_finalize", END)

    return builder.compile(checkpointer=checkpointer)
