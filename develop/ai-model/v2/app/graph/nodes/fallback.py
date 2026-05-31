"""Fallback 노드 — Layer 3 Fallback 상세 구현.

속도와 예측 가능성을 위해 fallback 재추론을 제거하고,
항상 clarification 응답으로 종료한다.
"""
from __future__ import annotations

import logging

from app.graph.deps import NodeDeps
from app.schemas.state import GraphState

logger = logging.getLogger(__name__)

_CLARIFICATION_RESPONSE = (
    "죄송해요, 질문을 정확히 이해하지 못했어요. "
    "조금 더 구체적으로 말씀해 주시겠어요? 예를 들어, "
    "운동 계획, 식단 기록, 건강 정보 등 어떤 도움이 필요하신지 알려주세요."
)
_PLAN_DOMAIN_CLARIFICATION_RESPONSE = (
    "운동 플랜인지 식단 플랜인지 먼저 정해주시면 바로 작성할게요. "
    "둘 다 원하시면 '운동과 식단 둘 다'처럼 말해 주세요."
)


def make_fallback_node(deps: NodeDeps):
    async def fallback_node(state: GraphState) -> dict:
        count = state.get("fallback_count", 0)
        logger.info("Fallback Clarification 요청: fallback_count=%d", count)
        deps.trace.record_current_event(
            stage="fallback",
            status="warn",
            title="Fallback clarification returned",
            detail={
                "fallback_count": count,
                "action_intent": state.get("action_intent"),
                "domain": state.get("domain"),
                "context_resolution": state.get("context_resolution"),
            },
        )
        response = _CLARIFICATION_RESPONSE
        diagnostics = state.get("routing_diagnostics") or {}
        if "ambiguous_mixed_plan_domain" in (diagnostics.get("reason_codes") or []):
            response = _PLAN_DOMAIN_CLARIFICATION_RESPONSE

        return {
            "response": response,
            "fallback_count": count + 1,
            "needs_clarification": True,
        }

    return fallback_node
