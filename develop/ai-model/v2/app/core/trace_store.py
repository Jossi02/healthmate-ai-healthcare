"""In-memory request tracing for observability pages."""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Callable

_current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


_REDACTED = "[REDACTED]"
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[^\s,;]+")
_CREDENTIAL_TEXT_RE = re.compile(
    r"(?i)\b(?:authorization|cookie|set[_-]?cookie|x[_-]?api[_-]?key|"
    r"internal[_-]?api[_-]?key|api[_-]?key|token|access[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|service[_-]?role[_-]?key|password[_-]?hash|signing[_-]?key|"
    r"secret[_-]?key|password|passwd|secret|jwt)\b[\"']?\s*[:=]\s*"
    r"(['\"]?)[^,\s'\"}]+"
)
_CREDENTIAL_KEY_MARKERS = {
    "apikey",
    "authorization",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "password",
    "passwd",
    "secret",
    "credential",
    "cookie",
    "setcookie",
    "privatekey",
    "clientsecret",
    "serviceaccount",
    "servicerolekey",
    "passwordhash",
    "signingkey",
    "secretkey",
    "accesskey",
    "jwt",
}
_SUMMARY_STRING_VALUES = {
    "intent": {
        "casual",
        "공감_케어",
        "기록",
        "계획",
        "수정",
        "계획_승인",
        "정보",
        "안전경고",
        "fallback",
        "home_recommendation",
        "care",
        "create",
        "modify",
        "approval",
        "record",
        "info",
        "safety",
        "plan",
    },
    "action_intent": {
        "care",
        "create",
        "modify",
        "approval",
        "record",
        "info",
        "safety",
        "fallback",
        "casual",
    },
    "domain": {"workout", "diet", "bundle", "general", "profile"},
    "support_mode": {"normal", "care"},
    "search_quality": {"ok", "weak", "degraded"},
    "record_type": {"profile", "plan_check", "plan_delete"},
    "modify_target": {"workout", "diet"},
    "proposed_plan_type": {"workout", "diet", "bundle"},
    "proposed_plan_action": {"create", "update"},
}
_SUMMARY_WRITE_TYPES = {"profile", "plan_check", "plan_create", "plan_update", "plan_delete"}
_EVIDENCE_STATUSES = {
    "not_required",
    "degraded",
    "degraded_fail_open",
    "missing",
    "grounded",
    "weak",
}
_QUALITY_CODE_CATEGORIES = {
    "profile_fit_warning_codes": "profile_fit_warning",
    "goal_fit_warning_codes": "goal_fit_warning",
    "critical_profile_fit_codes": "critical_profile_fit",
}


def _utcnow_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")


def _redact_text(value: str) -> str:
    value = _AUTH_SCHEME_RE.sub(_REDACTED, value)
    return _CREDENTIAL_TEXT_RE.sub(_REDACTED, value)


def _is_credential_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return normalized in _CREDENTIAL_KEY_MARKERS or normalized.endswith(
        ("token", "secret", "password", "apikey", "credential", "privatekey")
    )


def redact_credentials(value: Any) -> Any:
    """Return a recursively redacted copy suitable for trace storage."""
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_credential_key(key) else redact_credentials(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_credentials(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _response_text(value: Any) -> str:
    if isinstance(value, dict) and value.get("response") is not None:
        return str(value["response"])
    return str(value) if value is not None else ""


def _response_length(value: Any) -> int:
    return len(_response_text(value).strip())


def _summary_validation_report(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    summary: dict[str, Any] = {}
    if isinstance(value.get("passed"), bool):
        summary["passed"] = value["passed"]

    dimensions = value.get("quality_dimensions")
    if isinstance(dimensions, dict):
        safe_dimensions: dict[str, Any] = {}
        evidence_status = dimensions.get("evidence_status")
        if evidence_status in _EVIDENCE_STATUSES:
            safe_dimensions["evidence_status"] = evidence_status
        if isinstance(dimensions.get("requires_external"), bool):
            safe_dimensions["requires_external"] = dimensions["requires_external"]
        coverage = dimensions.get("profile_field_coverage")
        if isinstance(coverage, dict) and isinstance(coverage.get("present_count"), int):
            safe_dimensions["profile_field_coverage"] = {
                "present_count": max(0, coverage["present_count"])
            }
        for key, category in _QUALITY_CODE_CATEGORIES.items():
            codes = dimensions.get(key)
            if isinstance(codes, (list, tuple)) and codes:
                safe_dimensions[key] = [category]
        semantic = dimensions.get("semantic_judge")
        if isinstance(semantic, dict):
            safe_semantic: dict[str, Any] = {}
            if semantic.get("mode") in {"off", "observe", "blocking"}:
                safe_semantic["mode"] = semantic["mode"]
            if isinstance(semantic.get("issue_count"), int):
                safe_semantic["issue_count"] = max(0, semantic["issue_count"])
            if safe_semantic:
                safe_dimensions["semantic_judge"] = safe_semantic
        if safe_dimensions:
            summary["quality_dimensions"] = safe_dimensions
    return summary or None


def _summary_generation_flags(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    summary: dict[str, Any] = {}
    for key in (
        "semantic_fallback_applied",
        "semantic_fallback_revalidated",
        "safe_diet_fallback_applied",
        "safe_diet_fallback_revalidated",
    ):
        if isinstance(value.get(key), bool):
            summary[key] = value[key]
    summary["persona_style_violations"] = bool(value.get("persona_style_violations"))
    for key in (
        "semantic_fallback_revalidation_issue_count",
        "safe_diet_fallback_revalidation_issue_count",
    ):
        if isinstance(value.get(key), int):
            summary[key] = max(0, value[key])
    return summary or None


def _summary_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "intent",
        "action_intent",
        "domain",
        "support_mode",
        "ambiguous",
        "search_quality",
        "record_type",
        "modify_target",
        "resolved_persona_id",
        "search_results_count",
        "proposed_plan_type",
        "proposed_plan_action",
        "proposed_plan_count",
        "awaiting_plan_confirmation",
        "active_proposal_present",
        "recent_dialogue_turns",
        "pending_writes_count",
        "pending_write_types",
        "profile_write_pending",
        "needs_clarification",
    )
    summary: dict[str, Any] = {}
    for key in allowed:
        item = value.get(key)
        if key == "pending_write_types":
            if isinstance(item, list) and all(isinstance(entry, str) for entry in item):
                summary[key] = [entry for entry in item if entry in _SUMMARY_WRITE_TYPES][:20]
        elif isinstance(item, (bool, int, float)):
            summary[key] = item
        elif isinstance(item, str) and item in _SUMMARY_STRING_VALUES.get(key, set()):
            summary[key] = item
    validation_report = _summary_validation_report(value.get("validation_report"))
    if validation_report:
        summary["validation_report"] = validation_report
    draft_components = value.get("draft_components")
    if isinstance(draft_components, dict):
        summary["draft_components"] = {
            "safety_notes": bool(draft_components.get("safety_notes")),
            "suggested_action": bool(draft_components.get("suggested_action")),
        }
    generation_flags = _summary_generation_flags(value.get("generation_quality_flags"))
    if generation_flags:
        summary["generation_quality_flags"] = generation_flags
    if "pending_sequential_plan" in value and isinstance(
        value.get("pending_sequential_plan"), (bool, type(None))
    ):
        summary["pending_sequential_plan"] = value.get("pending_sequential_plan")
    return summary


def _summary_quality(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    if isinstance(value.get("score"), (int, float)):
        result["score"] = max(0.0, min(1.0, float(value["score"])))
    if value.get("grade") in {"pass", "review", "fail"}:
        result["grade"] = value["grade"]
    if isinstance(value.get("issue_count"), int):
        result["issue_count"] = max(0, value["issue_count"])
    return result


def _summary_export(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("enabled", "sent"):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    for key in ("child_run_count", "child_run_skipped_count"):
        if isinstance(value.get(key), int):
            result[key] = max(0, value[key])
    error_code = value.get("error_code")
    if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", error_code):
        result["error_code"] = error_code
    return result or None


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
    def __init__(
        self,
        max_traces: int = 120,
        max_logs: int = 1200,
        *,
        debug_enabled: bool = False,
        ttl_seconds: float = 60 * 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_traces = max(0, max_traces)
        self._max_logs = max(0, max_logs)
        self._debug_enabled = debug_enabled
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._trace_order: deque[str] = deque()
        self._traces: dict[str, dict[str, Any]] = {}
        self._trace_created_at: dict[str, datetime] = {}
        self._logs: deque[dict[str, Any]] = deque()
        self._log_created_at: deque[datetime] = deque()

    def _now(self) -> datetime:
        now = self._clock()
        if isinstance(now, (int, float)):
            now = datetime.fromtimestamp(now, timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _drop_trace(self, trace_id: str) -> None:
        self._traces.pop(trace_id, None)
        self._trace_created_at.pop(trace_id, None)

    def _prune_expired(self, now: datetime) -> None:
        expired = {
            trace_id
            for trace_id, created_at in self._trace_created_at.items()
            if (now - created_at).total_seconds() >= self._ttl_seconds
        }
        if expired:
            self._trace_order = deque(
                trace_id for trace_id in self._trace_order if trace_id not in expired
            )
            for trace_id in expired:
                self._drop_trace(trace_id)

        while self._logs and self._log_created_at:
            if (now - self._log_created_at[0]).total_seconds() < self._ttl_seconds:
                break
            self._logs.popleft()
            self._log_created_at.popleft()

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
        now = self._now()
        trace = {
            "trace_id": trace_id,
            "kind": kind,
            "status": "running",
            "started_at": _utcnow_iso(now),
            "completed_at": None,
            "metadata": {},
            "events": [],
            "alerts": [],
            "logs": [],
            "was_reads": [],
            "was_writes": [],
            "state_summary": None,
            "quality": None,
            "langsmith_export": None,
        }
        if self._debug_enabled:
            trace.update(
                {
                    "user_id": redact_credentials(user_id),
                    "session_id": redact_credentials(session_id),
                    "message": redact_credentials(message),
                    "request_payload": redact_credentials(request_payload)
                    if request_payload
                    else None,
                    "metadata": redact_credentials(metadata) if metadata else {},
                    "response": None,
                    "was_data": {
                        "user_profile": None,
                        "today_plan": None,
                        "workout_full_plan": None,
                        "diet_full_plan": None,
                    },
                }
            )
        else:
            trace.update(
                {
                    "message_length": len(message or ""),
                    "response_length": None,
                }
            )
            if metadata and isinstance(metadata, dict) and "entrypoint" in metadata:
                trace["metadata"] = {"entrypoint": redact_credentials(metadata["entrypoint"])}
        with self._lock:
            self._prune_expired(now)
            self._traces[trace_id] = trace
            self._trace_created_at[trace_id] = now
            self._trace_order.append(trace_id)
            while len(self._trace_order) > self._max_traces:
                expired_id = self._trace_order.popleft()
                self._drop_trace(expired_id)
        return trace_id

    def finish_trace(
        self,
        trace_id: str,
        *,
        status: str,
        response: dict[str, Any] | None = None,
        state_summary: dict[str, Any] | None = None,
    ) -> None:
        now = self._now()
        with self._lock:
            self._prune_expired(now)
            trace = self._traces.get(trace_id)
            if not trace:
                return
            trace["status"] = status
            trace["completed_at"] = _utcnow_iso(now)
            if response is not None:
                if self._debug_enabled:
                    trace["response"] = redact_credentials(response)
                else:
                    trace["response_length"] = _response_length(response)
                    response_text = _response_text(response).casefold()
                    trace["response_flags"] = {
                        "fallback_or_error_language": any(
                            marker in response_text
                            for marker in ("오류", "다시 시도", "error", "failed", "fallback")
                        ),
                        "plan_sync_applied": (
                            response.get("plan_sync_applied")
                            if isinstance(response, dict)
                            and isinstance(response.get("plan_sync_applied"), bool)
                            else None
                        ),
                    }
            if state_summary is not None:
                trace["state_summary"] = (
                    redact_credentials(state_summary)
                    if self._debug_enabled
                    else _summary_state(state_summary)
                )

    def update_metadata(self, trace_id: str, **fields: Any) -> None:
        now = self._now()
        with self._lock:
            self._prune_expired(now)
            trace = self._traces.get(trace_id)
            if not trace:
                return
            for key, value in fields.items():
                if self._debug_enabled:
                    trace[key] = redact_credentials(value)
                elif key == "langsmith_export":
                    trace[key] = _summary_export(value)

    def record_quality(self, trace_id: str, quality: dict[str, Any]) -> None:
        now = self._now()
        with self._lock:
            self._prune_expired(now)
            trace = self._traces.get(trace_id)
            if not trace:
                return
            trace["quality"] = (
                redact_credentials(quality)
                if self._debug_enabled
                else _summary_quality(quality)
            )

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
        now = self._now()
        event = {
            "timestamp": _utcnow_iso(now),
            "stage": _redact_text(stage),
            "status": _redact_text(status),
            "duration_ms": duration_ms,
        }
        if self._debug_enabled:
            event.update(
                {
                    "title": _redact_text(title),
                    "detail": redact_credentials(detail) if detail else {},
                }
            )
        with self._lock:
            self._prune_expired(now)
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
        now = self._now()
        alert = {
            "timestamp": _utcnow_iso(now),
            "severity": _redact_text(severity),
        }
        if self._debug_enabled:
            alert.update(
                {
                    "message": _redact_text(message),
                    "detail": redact_credentials(detail) if detail else {},
                }
            )
        with self._lock:
            self._prune_expired(now)
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
        now = self._now()
        item = {
            "timestamp": _utcnow_iso(now),
            "method": _redact_text(method),
            "status": _redact_text(status),
            "duration_ms": duration_ms,
        }
        if self._debug_enabled:
            item.update(
                {
                    "path": _redact_text(path),
                    "error": _redact_text(error) if error is not None else None,
                    "request_body": redact_credentials(request_body),
                    "response_body": redact_credentials(response_body),
                }
            )
        with self._lock:
            self._prune_expired(now)
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
            if self._debug_enabled and snapshot_key and response_body is not None:
                trace["was_data"][snapshot_key] = redact_credentials(response_body)

    def add_log(self, log_entry: dict[str, Any]) -> None:
        now = self._now()
        safe_entry = redact_credentials(log_entry)
        if not self._debug_enabled:
            safe_entry = {
                key: safe_entry[key]
                for key in ("timestamp", "level", "logger", "trace_id")
                if key in safe_entry
            }
        with self._lock:
            self._prune_expired(now)
            self._logs.append(safe_entry)
            self._log_created_at.append(now)
            while len(self._logs) > self._max_logs:
                self._logs.popleft()
                self._log_created_at.popleft()
            trace_id = safe_entry.get("trace_id")
            if trace_id and trace_id in self._traces:
                self._traces[trace_id]["logs"].append(safe_entry)
                self._traces[trace_id]["logs"] = self._traces[trace_id]["logs"][-120:]

    def list_traces(self, limit: int = 30) -> list[dict[str, Any]]:
        now = self._now()
        with self._lock:
            self._prune_expired(now)
            ids = list(self._trace_order)[-limit:]
            traces = [self._traces[trace_id] for trace_id in reversed(ids) if trace_id in self._traces]
            result = []
            for trace in traces:
                item = {
                    "trace_id": trace["trace_id"],
                    "kind": trace["kind"],
                    "status": trace["status"],
                    "started_at": trace["started_at"],
                    "completed_at": trace["completed_at"],
                    "alert_count": len(trace["alerts"]),
                    "event_count": len(trace["events"]),
                    "summary": _build_list_summary(trace),
                }
                if self._debug_enabled:
                    item.update(
                        {
                            "user_id": trace.get("user_id"),
                            "session_id": trace.get("session_id"),
                            "message": trace.get("message"),
                        }
                    )
                else:
                    item.update(
                        {
                            "message_length": trace.get("message_length", 0),
                        }
                    )
                result.append(item)
            return result

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        now = self._now()
        with self._lock:
            self._prune_expired(now)
            trace = self._traces.get(trace_id)
            return redact_credentials(trace) if trace else None

    def list_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        now = self._now()
        with self._lock:
            self._prune_expired(now)
            return redact_credentials(list(self._logs)[-limit:])


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
