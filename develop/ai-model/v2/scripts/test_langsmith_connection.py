from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.core.trace_store import TraceStore
from app.services.langsmith_quality import (
    LangSmithQualityExporter,
    export_quality_trace,
    record_quality_for_trace,
)


def _print_json(label: str, payload: dict) -> None:
    print(f"{label}: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


async def main() -> int:
    settings = get_settings()
    exporter = LangSmithQualityExporter.from_settings(settings)

    config_status = {
        "enabled": exporter.enabled,
        "configured": exporter.configured,
        "endpoint": exporter.api_url,
        "project": exporter.project_name,
        "send_full_text": exporter.send_full_text,
        "api_key_present": bool(exporter.api_key),
    }
    _print_json("[langsmith-connection] config", config_status)

    if not exporter.enabled:
        print("[langsmith-connection] failed: LANGSMITH_QUALITY_ENABLED is false")
        return 2
    if not exporter.api_key:
        print("[langsmith-connection] failed: LangSmith API key is missing")
        return 2

    trace_store = TraceStore()
    trace_id = trace_store.start_trace(
        kind="chat",
        user_id="langsmith-smoke-user",
        session_id="langsmith-smoke-session",
        message="이번 주 운동 플랜 작성해줘",
        request_payload={"user_message": "이번 주 운동 플랜 작성해줘"},
        metadata={"smoke_test": True, "source": "scripts/test_langsmith_connection.py"},
    )
    trace_store.record_event(
        trace_id,
        stage="intent",
        status="ok",
        title="Smoke intent resolved",
        detail={
            "raw_intent": "계획",
            "coerced_intent": "계획",
            "confidence": 0.99,
        },
        duration_ms=3.2,
    )
    trace_store.record_event(
        trace_id,
        stage="search",
        status="ok",
        title="Smoke retrieval completed",
        detail={"targets": ["profile_constraints", "workout_guidelines"]},
        duration_ms=8.4,
    )
    trace_store.record_event(
        trace_id,
        stage="generate",
        status="ok",
        title="Smoke persona-aware response finalized",
        detail={"resolved_persona_id": "cheer_sis", "response_length": 38},
        duration_ms=12.7,
    )
    trace_store.finish_trace(
        trace_id,
        status="response_sent",
        response={"response": "이번 주는 무릎 부담을 낮춘 하체+유산소 루틴으로 작성할게요."},
        state_summary={
            "intent": "계획",
            "action_intent": "create",
            "domain": "workout",
            "search_quality": "ok",
            "search_results_count": 2,
            "proposed_plan_count": 3,
            "pending_writes_count": 0,
            "needs_clarification": False,
            "resolved_persona_id": "cheer_sis",
            "proposed_plan_type": "workout",
            "proposed_plan_action": "create",
            "draft_components": {"suggested_action": "이 운동 플랜으로 작성할까요?"},
        },
    )
    quality = record_quality_for_trace(trace_store, trace_id)
    _print_json(
        "[langsmith-connection] quality",
        {
            "trace_id": trace_id,
            "score": quality.get("score") if quality else None,
            "grade": quality.get("grade") if quality else None,
        },
    )

    await export_quality_trace(
        exporter=exporter,
        trace_store=trace_store,
        trace_id=trace_id,
    )
    trace = trace_store.get_trace(trace_id) or {}
    export_result = trace.get("langsmith_export") or {}
    _print_json("[langsmith-connection] export", export_result)

    if export_result.get("sent"):
        print("[langsmith-connection] passed")
        return 0

    alerts = trace.get("alerts") or []
    if alerts:
        _print_json("[langsmith-connection] alerts", {"alerts": alerts})
    print("[langsmith-connection] failed: export was not sent")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
