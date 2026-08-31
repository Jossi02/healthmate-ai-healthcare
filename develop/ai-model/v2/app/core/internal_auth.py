from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.core.config import get_settings


async def require_internal_api_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected_api_key = settings.INTERNAL_API_KEY

    if not expected_api_key:
        raise HTTPException(status_code=503, detail="Internal authentication is unavailable")

    if not isinstance(x_api_key, str) or not secrets.compare_digest(
        x_api_key.encode(), expected_api_key.encode()
    ):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")
