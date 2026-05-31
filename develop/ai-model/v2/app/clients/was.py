"""WAS HTTP 클라이언트 — Layer 4 API 엔드포인트 구현.

읽기 API (동기 호출):
  GET /api/user/profile
  GET /api/plan/today
  GET /api/workout-plan/full
  GET /api/diet-plan/full

쓰기 API (비동기 호출):
  PUT  /api/user/profile
  POST /api/plan/create
  PUT  /api/plan/update
  DELETE /api/plan/delete
  PUT  /api/plan/check
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.exceptions import ExternalServiceError
from app.core.trace_store import TraceStore, get_current_trace_id


class WASClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        api_key: str | None = None,
        trace_store: TraceStore | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._api_key = api_key
        self._trace_store = trace_store

    # ── 읽기 API ─────────────────────────────────────────────────────────────

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        return await self._get(f"/api/user/profile/{user_id}")

    async def get_today_plan(self, user_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/api/plan/today/{user_id}")

    async def get_workout_plan_full(self, user_id: str) -> dict[str, Any]:
        return await self._get(f"/api/workout-plan/full/{user_id}")

    async def get_diet_plan_full(self, user_id: str) -> dict[str, Any]:
        return await self._get(f"/api/diet-plan/full/{user_id}")

    # ── 쓰기 API ─────────────────────────────────────────────────────────────

    async def put_user_profile(self, user_id: str, changes: dict[str, Any]) -> None:
        await self._put(f"/api/user/profile/{user_id}", changes)

    async def post_plan_create(self, user_id: str, plan: dict[str, Any]) -> None:
        await self._post(f"/api/plan/create/{user_id}", plan)

    async def put_plan_update(self, user_id: str, plan: dict[str, Any]) -> None:
        await self._put(f"/api/plan/update/{user_id}", plan)

    async def delete_plan(self, user_id: str, payload: dict[str, Any]) -> None:
        await self._delete(f"/api/plan/delete/{user_id}", payload)

    async def put_plan_check(
        self,
        user_id: str,
        item_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"item_id": item_id}
        if idempotency_key:
            payload["_idempotency_key"] = idempotency_key
        await self._put(f"/api/plan/check/{user_id}", payload)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    async def _get(self, path: str) -> Any:
        started_at = time.perf_counter()
        trace_id = get_current_trace_id()
        try:
            resp = await self._client.get(
                f"{self._base_url}{path}",
                headers=self._build_headers(),
            )
            resp.raise_for_status()
            payload = self._extract_payload(resp.json())
            self._record_trace(
                trace_id,
                method="GET",
                path=path,
                status="ok",
                duration_ms=time.perf_counter() - started_at,
                response_body=payload,
            )
            return payload
        except httpx.TimeoutException:
            self._record_trace(
                trace_id,
                method="GET",
                path=path,
                status="timeout",
                duration_ms=time.perf_counter() - started_at,
                error="request timeout",
            )
            raise ExternalServiceError(service="WAS", message="request timeout")
        except httpx.HTTPStatusError as exc:
            self._record_trace(
                trace_id,
                method="GET",
                path=path,
                status=f"http_{exc.response.status_code}",
                duration_ms=time.perf_counter() - started_at,
                error=f"HTTP {exc.response.status_code}",
            )
            raise ExternalServiceError(
                service="WAS",
                message=f"HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
            )
        except httpx.RequestError as exc:
            self._record_trace(
                trace_id,
                method="GET",
                path=path,
                status="request_error",
                duration_ms=time.perf_counter() - started_at,
                error=str(exc),
            )
            raise ExternalServiceError(service="WAS", message="request error")

    def _extract_payload(self, response_json: Any) -> Any:
        if isinstance(response_json, dict):
            return response_json.get("data", response_json)
        return response_json

    async def _post(self, path: str, body: dict) -> None:
        started_at = time.perf_counter()
        trace_id = get_current_trace_id()
        try:
            resp = await self._client.post(
                f"{self._base_url}{path}",
                json=body,
                headers=self._build_headers(),
            )
            resp.raise_for_status()
            self._record_trace(
                trace_id,
                method="POST",
                path=path,
                status="ok",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
            )
        except httpx.TimeoutException:
            self._record_trace(
                trace_id,
                method="POST",
                path=path,
                status="timeout",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error="request timeout",
            )
            raise ExternalServiceError(service="WAS", message="request timeout")
        except httpx.HTTPStatusError as exc:
            self._record_trace(
                trace_id,
                method="POST",
                path=path,
                status=f"http_{exc.response.status_code}",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error=f"HTTP {exc.response.status_code}",
            )
            raise ExternalServiceError(
                service="WAS",
                message=f"HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
            )
        except httpx.RequestError as exc:
            self._record_trace(
                trace_id,
                method="POST",
                path=path,
                status="request_error",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error=str(exc),
            )
            raise ExternalServiceError(service="WAS", message="request error")

    async def _put(self, path: str, body: dict) -> None:
        started_at = time.perf_counter()
        trace_id = get_current_trace_id()
        try:
            resp = await self._client.put(
                f"{self._base_url}{path}",
                json=body,
                headers=self._build_headers(),
            )
            resp.raise_for_status()
            self._record_trace(
                trace_id,
                method="PUT",
                path=path,
                status="ok",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
            )
        except httpx.TimeoutException:
            self._record_trace(
                trace_id,
                method="PUT",
                path=path,
                status="timeout",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error="request timeout",
            )
            raise ExternalServiceError(service="WAS", message="request timeout")
        except httpx.HTTPStatusError as exc:
            self._record_trace(
                trace_id,
                method="PUT",
                path=path,
                status=f"http_{exc.response.status_code}",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error=f"HTTP {exc.response.status_code}",
            )
            raise ExternalServiceError(
                service="WAS",
                message=f"HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
            )
        except httpx.RequestError as exc:
            self._record_trace(
                trace_id,
                method="PUT",
                path=path,
                status="request_error",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error=str(exc),
            )
            raise ExternalServiceError(service="WAS", message="request error")

    async def _delete(self, path: str, body: dict) -> None:
        started_at = time.perf_counter()
        trace_id = get_current_trace_id()
        try:
            resp = await self._client.request(
                "DELETE",
                f"{self._base_url}{path}",
                json=body,
                headers=self._build_headers(),
            )
            resp.raise_for_status()
            self._record_trace(
                trace_id,
                method="DELETE",
                path=path,
                status="ok",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
            )
        except httpx.TimeoutException:
            self._record_trace(
                trace_id,
                method="DELETE",
                path=path,
                status="timeout",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error="request timeout",
            )
            raise ExternalServiceError(service="WAS", message="request timeout")
        except httpx.HTTPStatusError as exc:
            self._record_trace(
                trace_id,
                method="DELETE",
                path=path,
                status=f"http_{exc.response.status_code}",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error=f"HTTP {exc.response.status_code}",
            )
            raise ExternalServiceError(
                service="WAS",
                message=f"HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
            )
        except httpx.RequestError as exc:
            self._record_trace(
                trace_id,
                method="DELETE",
                path=path,
                status="request_error",
                duration_ms=time.perf_counter() - started_at,
                request_body=body,
                error=str(exc),
            )
            raise ExternalServiceError(service="WAS", message="request error")

    def _record_trace(
        self,
        trace_id: str | None,
        *,
        method: str,
        path: str,
        status: str,
        duration_ms: float,
        request_body: dict[str, Any] | None = None,
        response_body: Any = None,
        error: str | None = None,
    ) -> None:
        if not trace_id or not self._trace_store:
            return
        self._trace_store.record_was_call(
            trace_id,
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration_ms * 1000, 2),
            request_body=request_body,
            response_body=response_body,
            error=error,
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers
