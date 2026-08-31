"""Offline security-boundary regression tests for AI v2."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.internal_auth import require_internal_api_key  # noqa: E402
from app.routers.debug import get_debug_page  # noqa: E402


def make_settings(**overrides) -> Settings:
    values = {
        "GEMINI_API_KEY": "test-gemini-key",
        "WAS_BASE_URL": "http://127.0.0.1:8080",
        "INTERNAL_API_KEY": "test-internal-key",
        "APP_ENV": "test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def app_environment(app_env: str, debug_enabled: bool) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GEMINI_API_KEY": "test-gemini-key",
            "WAS_BASE_URL": "http://127.0.0.1:8080",
            "INTERNAL_API_KEY": "i" * 32,
            "APP_ENV": app_env,
            "ENABLE_DEBUG_ROUTES": str(debug_enabled).lower(),
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
        }
    )
    return env


def app_import(app_env: str, debug_enabled: bool, *, with_key: bool = True) -> subprocess.CompletedProcess[str]:
    env = app_environment(app_env, debug_enabled)
    if not with_key:
        env["INTERNAL_API_KEY"] = ""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from app.main import app; "
            "print(json.dumps(sorted(app.openapi()['paths'])))",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def route_paths(app_env: str, debug_enabled: bool) -> set[str]:
    result = app_import(app_env, debug_enabled)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


class SettingsSecurityTests(unittest.TestCase):
    def test_internal_key_is_required(self) -> None:
        with self.assertRaises(ValidationError):
            make_settings(INTERNAL_API_KEY=None)

    def test_blank_and_placeholder_keys_are_rejected(self) -> None:
        for value in ("   ", "your-internal-api-key"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                make_settings(INTERNAL_API_KEY=value)

    def test_short_production_key_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            make_settings(APP_ENV="production", INTERNAL_API_KEY="short")

    def test_defaults_are_fail_safe(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                GEMINI_API_KEY="test-gemini-key",
                WAS_BASE_URL="http://127.0.0.1:8080",
                INTERNAL_API_KEY="i" * 32,
            )
        self.assertEqual(settings.APP_ENV, "production")
        self.assertFalse(settings.ENABLE_DEBUG_ROUTES)


class InternalAuthTests(unittest.TestCase):
    def test_missing_server_key_fails_closed(self) -> None:
        with patch("app.core.internal_auth.get_settings", return_value=SimpleNamespace(INTERNAL_API_KEY=None)):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(require_internal_api_key("anything"))
        self.assertEqual(caught.exception.status_code, 503)

    def test_missing_and_wrong_request_keys_are_forbidden(self) -> None:
        settings = SimpleNamespace(INTERNAL_API_KEY="expected")
        with patch("app.core.internal_auth.get_settings", return_value=settings):
            for value in (None, "wrong", "잘못된-키"):
                with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                    asyncio.run(require_internal_api_key(value))
                self.assertEqual(caught.exception.status_code, 403)

    def test_matching_key_passes(self) -> None:
        settings = SimpleNamespace(INTERNAL_API_KEY="expected")
        with patch("app.core.internal_auth.get_settings", return_value=settings):
            asyncio.run(require_internal_api_key("expected"))


class DebugRouteTests(unittest.TestCase):
    DEBUG_PATHS = {
        "/debug",
        "/debug/observability",
        "/debug/api/traces",
        "/debug/api/traces/{trace_id}",
        "/debug/api/logs",
    }

    def test_ai_app_startup_rejects_missing_internal_key(self) -> None:
        result = app_import("production", False, with_key=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INTERNAL_API_KEY", result.stderr)

    def test_production_cannot_enable_debug_routes(self) -> None:
        self.assertTrue(self.DEBUG_PATHS.isdisjoint(route_paths("production", True)))

    def test_development_requires_explicit_opt_in(self) -> None:
        self.assertTrue(self.DEBUG_PATHS.isdisjoint(route_paths("development", False)))

    def test_development_and_local_opt_in_mount_debug_routes(self) -> None:
        for app_env in ("development", "local"):
            with self.subTest(app_env=app_env):
                self.assertTrue(self.DEBUG_PATHS.issubset(route_paths(app_env, True)))


class DisclosureTests(unittest.TestCase):
    def test_rendered_debug_page_contains_no_service_key(self) -> None:
        secret = "rendered-page-secret-must-not-appear"
        with patch.dict(os.environ, {"INTERNAL_API_KEY": secret}):
            html = asyncio.run(get_debug_page())
        self.assertNotIn(secret, html)
        self.assertNotIn("x-api-key", html.casefold())

    def test_profile_override_cannot_enable_debug_state(self) -> None:
        source = (ROOT / "app" / "routers" / "chat.py").read_text(encoding="utf-8")
        self.assertNotIn("bool(req.user_profile_override)", source)
        self.assertIn('settings.APP_ENV.strip().casefold() in {"development", "local"}', source)


if __name__ == "__main__":
    unittest.main()
