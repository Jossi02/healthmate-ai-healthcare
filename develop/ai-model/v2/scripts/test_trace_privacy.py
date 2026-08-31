"""Offline privacy and retention checks for the AI TraceStore."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.trace_store import TraceStore  # noqa: E402


class TraceStorePrivacyTests(unittest.TestCase):
    def test_summary_omits_raw_fields_and_allowlists_state(self) -> None:
        now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        store = TraceStore(clock=lambda: now[0])
        trace_id = store.start_trace(
            kind="chat",
            user_id="private-user",
            session_id="private-session",
            message="private-message",
            request_payload={"prompt": "private-payload"},
        )
        store.record_event(
            trace_id,
            stage="generate",
            title="private-title",
            detail={"raw": "private-event"},
            duration_ms=12,
        )
        store.record_was_call(
            trace_id,
            method="GET",
            path="/api/user/profile/private-user",
            status="ok",
            duration_ms=4,
            request_body={"raw": "private-request"},
            response_body={"raw": "private-response"},
        )
        store.finish_trace(
            trace_id,
            status="response_sent",
            response={"response": "private-response"},
            state_summary={
                "intent": "info",
                "search_results_count": 1,
                "profile_constraints": {"allergies": ["private-health"]},
                "search_results_preview": [{"text": "private-health"}],
                "draft_components": {
                    "safety_notes": ["private-health"],
                    "suggested_action": "private-action",
                },
                "validation_report": {
                    "passed": True,
                    "raw": "private-validation",
                    "quality_dimensions": {
                        "evidence_status": "degraded_fail_open",
                        "requires_external": True,
                        "profile_field_coverage": {
                            "present_count": 2,
                            "fields": ["private-health"],
                        },
                        "profile_fit_warning_codes": ["medical_history"],
                        "semantic_judge": {
                            "mode": "blocking",
                            "issue_count": 1,
                            "raw": "private-semantic",
                        },
                    },
                },
                "generation_quality_flags": {
                    "semantic_fallback_applied": True,
                    "persona_style_violations": ["private-style"],
                },
            },
        )
        store.record_quality(
            trace_id,
            {"score": 0.9, "grade": "pass", "issues": [{"detail": "private-quality"}]},
        )
        trace = store.get_trace(trace_id)

        self.assertIsNotNone(trace)
        self.assertNotIn("private", repr(trace))
        for field in ("user_id", "session_id", "message", "request_payload", "response", "was_data"):
            self.assertNotIn(field, trace)
        self.assertEqual(
            trace["state_summary"],
            {
                "intent": "info",
                "search_results_count": 1,
                "validation_report": {
                    "passed": True,
                    "quality_dimensions": {
                        "evidence_status": "degraded_fail_open",
                        "requires_external": True,
                        "profile_field_coverage": {"present_count": 2},
                        "profile_fit_warning_codes": ["profile_fit_warning"],
                        "semantic_judge": {"mode": "blocking", "issue_count": 1},
                    },
                },
                "draft_components": {"safety_notes": True, "suggested_action": True},
                "generation_quality_flags": {
                    "semantic_fallback_applied": True,
                    "persona_style_violations": True,
                },
            },
        )
        self.assertEqual(trace["response_flags"]["fallback_or_error_language"], False)
        self.assertEqual(trace["response_flags"]["plan_sync_applied"], None)
        self.assertNotIn("medical_history", repr(trace))
        self.assertNotIn("issues", trace["quality"])
        self.assertNotIn("detail", trace["events"][0])
        self.assertNotIn("path", trace["was_reads"][0])

        unsafe_code_trace = store.start_trace(kind="chat")
        store.finish_trace(
            unsafe_code_trace,
            status="response_sent",
            state_summary={
                "validation_report": {
                    "quality_dimensions": {"evidence_status": "diabetes"}
                }
            },
        )
        self.assertNotIn("diabetes", repr(store.get_trace(unsafe_code_trace)))

    def test_debug_preserves_fields_but_redacts_nested_credentials(self) -> None:
        store = TraceStore(debug_enabled=True)
        trace_id = store.start_trace(
            kind="chat",
            message="debug-message",
            request_payload={
                "plain": "keep",
                "headers": {"Authorization": "Bearer request-secret"},
                "nested": [
                    {
                        "api_key": "api-secret",
                        "x-api-key": "x-api-secret",
                        "internalApiKey": "internal-secret",
                        "accessToken": "access-secret",
                        "refresh_token": "refresh-secret",
                        "Cookie": "cookie-secret",
                        "passwordHash": "camel-hash-secret",
                        "password_hash": "hash-secret",
                        "jwt": "jwt-secret",
                        "secret": "generic-secret",
                        "service_role_key": "role-secret",
                    }
                ],
            },
        )
        store.record_event(
            trace_id,
            stage="generate",
            title="debug-title",
            detail={"nested": {"password": "password-secret", "plain": "keep"}},
        )
        store.record_was_call(
            trace_id,
            method="GET",
            path="/api/profile",
            status="ok",
            duration_ms=1,
            response_body={"nested": [{"client_secret": "client-secret"}]},
        )
        store.add_log(
            {
                "timestamp": "now",
                "level": "ERROR",
                "logger": "test",
                "trace_id": trace_id,
                "message": (
                    "Cookie: cookie-secret token=log-token-secret "
                    "internalApiKey=log-api-secret serviceRoleKey=log-role-secret "
                    '{"password_hash":"json-hash-secret",'
                    '"serviceRoleKey":"json-role-secret"}'
                ),
            }
        )
        store.finish_trace(
            trace_id,
            status="response_sent",
            response={"response": "debug-response", "nested": {"token": "response-secret"}},
        )
        trace = store.get_trace(trace_id)

        self.assertEqual(trace["message"], "debug-message")
        self.assertEqual(trace["request_payload"]["plain"], "keep")
        self.assertEqual(trace["request_payload"]["headers"]["Authorization"], "[REDACTED]")
        for key in (
            "api_key",
            "x-api-key",
            "internalApiKey",
            "accessToken",
            "refresh_token",
            "Cookie",
            "passwordHash",
            "password_hash",
            "jwt",
            "secret",
            "service_role_key",
        ):
            self.assertEqual(trace["request_payload"]["nested"][0][key], "[REDACTED]")
        self.assertEqual(trace["events"][0]["detail"]["nested"]["password"], "[REDACTED]")
        self.assertEqual(trace["was_reads"][0]["response_body"]["nested"][0]["client_secret"], "[REDACTED]")
        self.assertEqual(trace["response"]["nested"]["token"], "[REDACTED]")
        for secret in (
            "request-secret",
            "api-secret",
            "cookie-secret",
            "log-token-secret",
            "log-api-secret",
            "log-role-secret",
            "json-hash-secret",
            "json-role-secret",
        ):
            self.assertNotIn(secret, repr(trace))

        error_payload = store.start_trace(
            kind="chat",
            request_payload={"error": ValueError('password_hash="error-secret"')},
        )
        self.assertNotIn("error-secret", repr(store.get_trace(error_payload)))

    def test_ttl_and_count_bounds_apply_to_traces_and_logs(self) -> None:
        now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        store = TraceStore(max_traces=2, max_logs=2, ttl_seconds=10, clock=lambda: now[0])
        trace_ids = [store.start_trace(kind="chat", message=str(index)) for index in range(3)]
        self.assertEqual(len(store.list_traces()), 2)
        for index in range(3):
            store.add_log({"timestamp": "now", "level": "INFO", "logger": "test", "message": str(index)})
        self.assertEqual(len(store.list_logs()), 2)
        for index in range(121):
            store.add_log(
                {
                    "timestamp": "now",
                    "level": "INFO",
                    "logger": "test",
                    "message": str(index),
                    "trace_id": trace_ids[-1],
                }
            )
        self.assertEqual(len(store.get_trace(trace_ids[-1])["logs"]), 120)
        now[0] += timedelta(seconds=10)
        self.assertEqual(store.list_traces(), [])
        self.assertIsNone(store.get_trace(trace_ids[-1]))
        self.assertEqual(store.list_logs(), [])

if __name__ == "__main__":
    unittest.main()
