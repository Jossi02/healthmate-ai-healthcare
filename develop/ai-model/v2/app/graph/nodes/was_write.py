"""Background WAS write helpers executed after the response is returned."""
from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from app.core.exceptions import ExternalServiceError
from app.graph.deps import NodeDeps
from app.graph.nodes.intent import INTENT_APPROVAL, INTENT_RECORD
from app.schemas.llm_responses import PlanExtractResponse, PlanModifyResponse
from app.schemas.state import PendingWrite
from app.schemas.was import to_plan_create_batches, to_plan_update_batches

logger = logging.getLogger(__name__)

_PLAN_EXTRACT_PROMPT = """\
Extract a structured workout or diet plan from the AI response below.
If there is no concrete plan, return has_plan as false.

Return JSON in this format:
{
  "has_plan": true/false,
  "plan_type": "workout" | "diet",
  "items": [
    {
      "name": "Plan item name",
      "detail": "Short description",
      "day": "YYYY-MM-DD date",
      "ex_list": [{"exercise_name": "Exercise name", "sets": 3}]
    }
  ]
}
"""

_PLAN_MODIFY_PROMPT = """\
Compare the original plan with the AI revision response below and return the fully updated plan as JSON.
Do not return only changed fields. Return the complete final plan.
If there are no meaningful changes, return has_changes as false.

Return JSON in this format:
{
  "has_changes": true/false,
  "plan_type": "workout" | "diet",
  "items": [
    {
      "name": "Plan item name",
      "detail": "Short description",
      "day": "YYYY-MM-DD date",
      "ex_list": [{"exercise_name": "Exercise name", "sets": 3}]
    }
  ]
}
"""


class WriteExecutionResult(TypedDict):
    pending: list[PendingWrite]
    write_succeeded: bool


async def execute_was_writes(
    deps: NodeDeps,
    user_id: str,
    intent: str,
    response: str,
    record_type: str | None,
    profile_changes: dict[str, Any] | None,
    today_plan: list[dict] | None,
    search_results: list[dict] | None,
    modify_target: str | None,
    modify_plan_context: dict | None,
    proposed_plan: list[dict] | None,
    proposed_plan_type: str | None,
    proposed_plan_action: str | None,
) -> WriteExecutionResult:
    """Execute WAS writes and return pending retries plus approval success state."""

    del response, today_plan, search_results, modify_plan_context

    pending: list[PendingWrite] = []
    write_succeeded = False

    if intent == INTENT_RECORD and profile_changes:
        if record_type == "plan_check":
            item_id = profile_changes.get("item_id")
            if item_id:
                write: PendingWrite = {"write_type": "plan_check", "payload": profile_changes}
                try:
                    await deps.was.put_plan_check(user_id, item_id)
                    logger.info("plan_check WAS write succeeded: %s", item_id)
                except ExternalServiceError as exc:
                    logger.warning("plan_check WAS write failed: %s", exc)
                    pending.append(write)
        elif record_type == "profile":
            write = {"write_type": "profile", "payload": profile_changes}
            try:
                await deps.was.put_user_profile(user_id, profile_changes)
                logger.info("profile WAS write succeeded")
            except ExternalServiceError as exc:
                logger.warning("profile WAS write failed: %s", exc)
                pending.append(write)

    if intent == INTENT_APPROVAL and proposed_plan:
        resolved_plan_type = proposed_plan_type or modify_target or "workout"
        write_type = "plan_update" if proposed_plan_action == "update" else "plan_create"
        raw_payload = {
            "plan_type": resolved_plan_type,
            "items": proposed_plan,
        }

        try:
            if write_type == "plan_update":
                plan_payloads = to_plan_update_batches(
                    {
                        "has_changes": True,
                        **raw_payload,
                    }
                )
            else:
                plan_payloads = to_plan_create_batches(
                    {
                        "has_plan": True,
                        **raw_payload,
                    }
                )
        except Exception as exc:
            logger.exception("approval WAS payload generation failed: %s", exc)
            return {"pending": pending, "write_succeeded": False}

        if not plan_payloads:
            logger.warning("approval WAS write skipped: plan payload generation returned empty")
            return {"pending": pending, "write_succeeded": False}

        successful_writes = 0
        for plan_payload in plan_payloads:
            write = {"write_type": write_type, "payload": plan_payload}
            try:
                if write_type == "plan_update":
                    try:
                        await deps.was.put_plan_update(user_id, plan_payload)
                    except ExternalServiceError as exc:
                        if not exc.is_http_status(404):
                            raise
                        logger.info(
                            "approval WAS update missed existing plan; retrying as create (%s)",
                            plan_payload.get("plan_type"),
                        )
                        await deps.was.post_plan_create(user_id, plan_payload)
                else:
                    try:
                        await deps.was.post_plan_create(user_id, plan_payload)
                    except ExternalServiceError as exc:
                        if not exc.is_http_status(409):
                            raise
                        logger.info(
                            "approval WAS create conflicted; retrying as update (%s)",
                            plan_payload.get("plan_type"),
                        )
                        await deps.was.put_plan_update(user_id, plan_payload)
                logger.info(
                    "approval WAS write succeeded (%s:%s)",
                    write_type,
                    plan_payload.get("plan_type"),
                )
                successful_writes += 1
            except ExternalServiceError as exc:
                logger.warning(
                    "approval WAS write failed (%s:%s): %s",
                    write_type,
                    plan_payload.get("plan_type"),
                    exc,
                )
                pending.append(write)

        write_succeeded = successful_writes == len(plan_payloads) and successful_writes > 0

    return {"pending": pending, "write_succeeded": write_succeeded}


async def _extract_plan_from_response(deps: NodeDeps, response: str) -> dict | None:
    """Extract a structured plan from a free-form AI response."""

    try:
        raw = await deps.router.generate(
            system_prompt=_PLAN_EXTRACT_PROMPT,
            user_content=f"AI response:\n{response}",
            response_schema=PlanExtractResponse,
        )
        result = PlanExtractResponse.model_validate_json(raw)
        if result.has_plan and result.items:
            return result.model_dump()
        return None
    except Exception as exc:
        logger.warning("Plan extraction failed: %s", exc)
        return None


async def _extract_modified_plan(
    deps: NodeDeps, original_plan: dict, response: str
) -> dict | None:
    """Extract a fully modified plan from the original plan plus AI response."""

    original_json = json.dumps(original_plan, ensure_ascii=False)[:1000]
    try:
        raw = await deps.router.generate(
            system_prompt=_PLAN_MODIFY_PROMPT,
            user_content=f"Original plan:\n{original_json}\n\nAI revision response:\n{response}",
            response_schema=PlanModifyResponse,
        )
        result = PlanModifyResponse.model_validate_json(raw)
        if result.has_changes and result.items:
            return result.model_dump()
        return None
    except Exception as exc:
        logger.warning("Modified plan extraction failed: %s", exc)
        return None
