"""Checkpoint filtering for bounded/persistent conversation state only."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata, RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

_EPHEMERAL_CHANNELS = {
    "user_id",
    "user_message",
    "request_kind",
    "is_session_start",
    "intent",
    "action_intent",
    "domain",
    "support_mode",
    "ambiguous",
    "context_resolution",
    "confidence",
    "emotion",
    "requires_past_memory",
    "should_save_episode",
    "short_term_memory_query",
    "has_fact_change",
    "record_type",
    "profile_changes",
    "is_today",
    "modify_target",
    "search_targets",
    "modify_plan_context",
    "profile_constraints",
    "retrieval_decision",
    "search_results",
    "search_quality",
    "search_retry_count",
    "search_query",
    "draft_response",
    "draft_components",
    "home_recommendation_scope",
    "home_recommendations",
    "home_recommendation_recent",
    "resolved_persona_id",
    "response",
    "force_regenerate",
    "validation_report",
    "validation_retry_count",
    "self_eval_count",
    "self_eval_failure_reason",
    "needs_clarification",
    "summary",
    "messages",
    "last_assistant_message",
}


def _keep_channel(channel: str) -> bool:
    if channel.startswith("branch:"):
        return True
    return channel not in _EPHEMERAL_CHANNELS


def _filter_channel_map(channel_map: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in channel_map.items() if _keep_channel(key)}


def filter_checkpoint_payload(checkpoint: Checkpoint) -> Checkpoint:
    sanitized = dict(checkpoint)

    channel_values = checkpoint.get("channel_values")
    if isinstance(channel_values, dict):
        sanitized["channel_values"] = _filter_channel_map(channel_values)

    channel_versions = checkpoint.get("channel_versions")
    if isinstance(channel_versions, dict):
        sanitized["channel_versions"] = _filter_channel_map(channel_versions)

    updated_channels = checkpoint.get("updated_channels")
    if isinstance(updated_channels, list):
        sanitized["updated_channels"] = [channel for channel in updated_channels if _keep_channel(channel)]

    versions_seen = checkpoint.get("versions_seen")
    if isinstance(versions_seen, dict):
        sanitized["versions_seen"] = {
            node_name: _filter_channel_map(node_channels)
            if isinstance(node_channels, dict)
            else node_channels
            for node_name, node_channels in versions_seen.items()
        }

    return sanitized


def filter_checkpoint_writes(writes: Sequence[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [(channel, value) for channel, value in writes if _keep_channel(channel)]


class FilteringAsyncSqliteSaver(AsyncSqliteSaver):
    """SQLite saver that persists only bounded/persistent channels."""

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await super().aput(
            config,
            filter_checkpoint_payload(checkpoint),
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        filtered_writes = filter_checkpoint_writes(writes)
        if not filtered_writes:
            return
        await super().aput_writes(config, filtered_writes, task_id, task_path)
