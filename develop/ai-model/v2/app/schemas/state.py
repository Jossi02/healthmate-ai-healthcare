"""Shared LangGraph state for the v2 model."""
from __future__ import annotations

from typing import Any, Literal, Optional

from typing_extensions import NotRequired, TypedDict


class EmotionState(TypedDict):
    label: str
    intensity: float


class PendingWrite(TypedDict):
    write_type: str
    payload: dict[str, Any]
    write_id: NotRequired[str]
    idempotency_key: NotRequired[str]


class DraftComponents(TypedDict):
    core_message: str
    reason_points: list[str]
    suggested_action: str
    plan_preview: str
    safety_notes: list[str]
    approval_question: Optional[str]
    search_grounding_summary: str


RequestKind = Literal["chat", "home_recommendation"]
HomeRecommendationScope = Literal["all", "workout", "diet"]
ActionIntent = Literal[
    "create",
    "modify",
    "info",
    "record",
    "approval",
    "care",
    "casual",
    "safety",
    "fallback",
    "home_recommendation",
]
Domain = Literal["workout", "diet", "profile", "general", "none"]
SupportMode = Literal["care", "normal"]
ResolvedReference = Literal[
    "none",
    "active_proposal",
    "today_plan",
    "previous_answer",
    "recent_chat",
    "user_memory",
]
StateEffect = Literal[
    "none",
    "proposal_created",
    "proposal_updated",
    "proposal_approved",
    "profile_recorded",
    "plan_checked",
    "plan_deleted",
    "clarification_requested",
]
WriteMode = Literal["create", "update"]


class ContextResolution(TypedDict):
    resolved_reference: ResolvedReference
    resolved_domain: Domain
    resolved_text: str
    confidence: float
    ambiguous: bool


class ActiveProposal(TypedDict):
    domain: Literal["workout", "diet"]
    write_mode: WriteMode
    items: list[dict[str, Any]]
    summary: str
    last_used_turn: int


class RecentTurn(TypedDict):
    turn_id: int
    user_text: str
    assistant_text: str
    user_summary: str
    assistant_summary: str
    action_intent: ActionIntent
    domain: Domain
    support_mode: SupportMode
    referenced_object: ResolvedReference
    state_effect: StateEffect


class RecentDialogue(TypedDict):
    recent_turns: list[RecentTurn]


class GraphState(TypedDict):
    user_id: str
    user_message: str
    request_kind: RequestKind
    checkpoint_db_path: NotRequired[str]

    user_profile: Optional[dict[str, Any]]
    effective_user_profile: Optional[dict[str, Any]]
    pending_profile_overlay: Optional[dict[str, Any]]
    profile_override_applied: bool
    today_plan: Optional[list[dict[str, Any]]]

    turn_count: int
    is_session_start: bool

    intent: str
    action_intent: Optional[ActionIntent]
    domain: Domain
    support_mode: SupportMode
    ambiguous: bool
    routing_diagnostics: Optional[dict[str, Any]]
    context_resolution: ContextResolution
    confidence: float
    emotion: Optional[EmotionState]
    previous_intent: Optional[str]
    previous_emotion: Optional[EmotionState]

    requires_past_memory: bool
    should_save_episode: bool
    short_term_memory_query: bool
    has_fact_change: bool
    record_type: Optional[str]
    profile_changes: Optional[dict[str, Any]]
    is_today: Optional[bool]
    modify_target: Optional[str]
    search_targets: list[str]
    modify_plan_context: Optional[dict[str, Any]]
    profile_constraints: Optional[dict[str, Any]]
    retrieval_decision: Optional[dict[str, Any]]

    search_results: list[dict[str, Any]]
    search_quality: str
    search_retry_count: int
    search_query: Optional[str]

    pending_writes: list[PendingWrite]
    awaiting_plan_confirmation: bool
    active_proposal: Optional[ActiveProposal]
    recent_dialogue: RecentDialogue

    draft_response: Optional[str]
    draft_components: Optional[DraftComponents]
    proposed_plan: Optional[list[dict[str, Any]]]
    proposed_plan_type: Optional[str]
    proposed_plan_action: Optional[str]
    home_recommendation_scope: Optional[HomeRecommendationScope]
    home_recommendations: Optional[dict[str, Any]]
    home_recommendation_recent: Optional[dict[str, Any]]
    intimacy_level: int
    resolved_persona_id: Optional[str]
    profile_sync_version: int

    response: Optional[str]
    force_regenerate: bool
    validation_report: Optional[dict[str, Any]]
    validation_retry_count: int
    generation_quality_flags: Optional[dict[str, Any]]
    self_eval_count: int
    self_eval_failure_reason: Optional[str]

    fallback_count: int
    needs_clarification: bool
