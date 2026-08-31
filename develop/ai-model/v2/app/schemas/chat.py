"""FastAPI /chat 엔드포인트 요청/응답 스키마."""
from typing import Any, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_id: str
    user_message: str
    session_id: Optional[str] = None  # Public conversation identifier; checkpoint keys are tenant-scoped internally.
    # -- Debug Overrides (개발/테스트용) --
    user_profile_override: Optional[dict[str, Any]] = None


class ChatResponse(BaseModel):
    status: str = "success"
    session_id: str
    response: str
    intent: Optional[str] = None
    emotion: Optional[dict[str, Any]] = None
    draft_response: Optional[str] = None
    plan_sync_applied: Optional[bool] = None
    was_write_status: Optional[dict[str, Any]] = None
    pending_writes_count: int = 0
    pending_write_types: list[str] = []
    debug_state: Optional[dict[str, Any]] = None
