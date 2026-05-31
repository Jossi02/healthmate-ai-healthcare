"""In-memory request tracing for observability pages."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar, Token
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

_current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def bind_trace(trace_id: str | None) -> Token[str | None]:
    return _current_trace_id.set(trace_id)


def reset_trace(token: Token[str | None]) -> None:
    _current_trace_id.reset(token)


def get_current_trace_id() -> str | None:
    return _current_trace_id.get()


def timed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _build_list_summary(trace: dict[str, Any]) -> dict[str, Any]:
    state_summary = trace.get("state_summary") or {}
    slowest_label = None
    slowest_duration_ms = None

    for event in trace.get("events", []):
        duration_ms = event.get("duration_ms")
        if duration_ms is None:
            continue
        if slowest_duration_ms is None or duration_ms > slowest_duration_ms:
            slowest_duration_ms = duration_ms
            slowest_label = event.get("stage") or event.get("title")

    for item in [*(trace.get("was_reads") or []), *(trace.get("was_writes") or [])]:
        duration_ms = item.get("duration_ms")
        if duration_ms is None:
            continue
        if slowest_duration_ms is None or duration_ms > slowest_duration_ms:
            slowest_duration_ms = duration_ms
            slowest_label = f'{item.get("method", "WAS")} {item.get("path", "")}'.strip()

    return {
        "intent": state_summary.get("intent"),
        "search_quality": state_summary.get("search_quality"),
        "search_results_count": state_summary.get("search_results_count"),
        "modify_target": state_summary.get("modify_target"),
        "resolved_persona_id": state_summary.get("resolved_persona_id"),
        "retrieval_decision": state_summary.get("retrieval_decision"),
        "validation_passed": (state_summary.get("validation_report") or {}).get("passed")
        if isinstance(state_summary.get("validation_report"), dict)
        else None,
        "proposed_plan_type": state_summary.get("proposed_plan_type"),
        "proposed_plan_action": state_summary.get("proposed_plan_action"),
        "proposed_plan_count": state_summary.get("proposed_plan_count"),
        "pending_writes_count": state_summary.get("pending_writes_count"),
        "quality_score": (trace.get("quality") or {}).get("score"),
        "quality_grade": (trace.get("quality") or {}).get("grade"),
        "slowest_label": slowest_label,
        "slowest_duration_ms": slowest_duration_ms,
    }


class TraceStore:
    def __init__(self, max_traces: int = 120, max_logs: int = 1200) -> None:
        self._max_traces = max_traces
        self._max_logs = max_logs
        self._lock = threading.Lock()
        self._trace_order: deque[str] = deque()
        self._traces: dict[str, dict[str, Any]] = {}
        self._logs: deque[dict[str, Any]] = deque()

    def start_trace(
        self,
        *,
        kind: str,
        user_id: str | None = None,
        session_id: str | None = None,
        message: str | None = None,
        request_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        trace_id = uuid.uuid4().hex
        trace = {
            "trace_id": trace_id,
            "kind": kind,
            "status": "running",
            "started_at": _utcnow_iso(),
            "completed_at": None,
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "request_payload": deepcopy(request_payload) if request_payload else None,
            "metadata": deepcopy(metadata) if metadata else {},
            "events": [],
            "alerts": [],
            "logs": [],
            "was_reads": [],
            "was_writes": [],
            "was_data": {
                "user_profile": None,
                "today_plan": None,
                "workout_full_plan": None,
                "diet_full_plan": None,
            },
            "state_summary": None,
            "response": None,
            "quality": None,
            "langsmith_export": None,
        }
        with self._lock:
            self._traces[trace_id] = trace
            self._trace_order.append(trace_id)
            while len(self._trace_order) > self._max_traces:
                expired_id = self._trace_order.popleft()
                self._traces.pop(expired_id, None)
        return trace_id

    def finish_trace(
        self,
        trace_id: str,
        *,
        status: str,
        response: dict[str, Any] | None = None,
        state_summary: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            trace = self._traces.get(trace_id)
            if not trace:
                return
            trace["status"] = status
            trace["completed_at"] = _utcnow_iso()
            if response is not None:
                trace["response"] = deepcopy(response)
            if state_summary is not None:
                trace["state_summary"] = deepcopy(state_summary)

    def update_metadata(self, trace_id: str, **fields: Any) -> None:
        with self._lock:
            trace = self._traces.get(trace_id)
            if not trace:
                return
            for key, value in fields.items():
                trace[key] = deepcopy(value)

    def record_quality(self, trace_id: str, quality: dict[str, Any]) -> None:
        with self._lock:
            trace = self._traces.get(trace_id)
            if not trace:
                return
            trace["quality"] = deepcopy(quality)

    def record_event(
        self,
        trace_id: str,
        *,
        stage: str,
        status: str = "info",
        title: str,
        detail: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        event = {
            "timestamp": _utcnow_iso(),
            "stage": stage,
            "status": status,
            "title": title,
            "detail": deepcopy(detail) if detail else {},
            "duration_ms": duration_ms,
        }
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace:
                trace["events"].append(event)

    def record_current_event(
        self,
        *,
        stage: str,
        status: str = "info",
        title: str,
        detail: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        trace_id = get_current_trace_id()
        if trace_id:
            self.record_event(
                trace_id,
                stage=stage,
                status=status,
                title=title,
                detail=detail,
                duration_ms=duration_ms,
            )

    def record_alert(
        self,
        trace_id: str,
        *,
        severity: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        alert = {
            "timestamp": _utcnow_iso(),
            "severity": severity,
            "message": message,
            "detail": deepcopy(detail) if detail else {},
        }
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace:
                trace["alerts"].append(alert)

    def record_current_alert(
        self,
        *,
        severity: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        trace_id = get_current_trace_id()
        if trace_id:
            self.record_alert(
                trace_id,
                severity=severity,
                message=message,
                detail=detail,
            )

    def record_was_call(
        self,
        trace_id: str,
        *,
        method: str,
        path: str,
        status: str,
        duration_ms: float,
        request_body: dict[str, Any] | None = None,
        response_body: Any = None,
        error: str | None = None,
    ) -> None:
        item = {
            "timestamp": _utcnow_iso(),
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": duration_ms,
            "request_body": deepcopy(request_body) if request_body is not None else None,
            "response_body": deepcopy(response_body) if response_body is not None else None,
            "error": error,
        }
        with self._lock:
            trace = self._traces.get(trace_id)
            if not trace:
                return

            target = trace["was_reads"] if method == "GET" else trace["was_writes"]
            target.append(item)

            snapshot_key = None
            if method == "GET":
                if "/api/user/profile/" in path:
                    snapshot_key = "user_profile"
                elif "/api/plan/today/" in path:
                    snapshot_key = "today_plan"
                elif "/api/workout-plan/full/" in path:
                    snapshot_key = "workout_full_plan"
                elif "/api/diet-plan/full/" in path:
                    snapshot_key = "diet_full_plan"
            if snapshot_key and response_body is not None:
                trace["was_data"][snapshot_key] = deepcopy(response_body)

    def add_log(self, log_entry: dict[str, Any]) -> None:
        with self._lock:
            self._logs.append(log_entry)
            while len(self._logs) > self._max_logs:
                self._logs.popleft()
            trace_id = log_entry.get("trace_id")
            if trace_id and trace_id in self._traces:
                self._traces[trace_id]["logs"].append(log_entry)
                self._traces[trace_id]["logs"] = self._traces[trace_id]["logs"][-120:]

    def list_traces(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._trace_order)[-limit:]
            traces = [self._traces[trace_id] for trace_id in reversed(ids) if trace_id in self._traces]
            return [
                {
                    "trace_id": trace["trace_id"],
                    "kind": trace["kind"],
                    "status": trace["status"],
                    "started_at": trace["started_at"],
                    "completed_at": trace["completed_at"],
                    "user_id": trace["user_id"],
                    "session_id": trace["session_id"],
                    "message": trace["message"],
                    "alert_count": len(trace["alerts"]),
                    "event_count": len(trace["events"]),
                    "summary": _build_list_summary(trace),
                }
                for trace in traces
            ]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock:
            trace = self._traces.get(trace_id)
            return deepcopy(trace) if trace else None

    def list_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._logs)[-limit:])


class TraceLogHandler(logging.Handler):
    def __init__(self, store: TraceStore) -> None:
        super().__init__(level=logging.INFO)
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": _utcnow_iso(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "trace_id": get_current_trace_id(),
            }
            self._store.add_log(entry)
        except Exception:
            pass
