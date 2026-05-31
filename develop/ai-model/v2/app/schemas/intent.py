"""Flash-Lite 의도 분석 출력 스키마.

Layer 2 의도 분석 상세 기반.
모든 필드는 Flash-Lite가 JSON으로 반환한다.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EmotionOutput(BaseModel):
    label: str = Field(description="감정 레이블 (슬픔·기쁨·불안·분노·중립 등)")
    intensity: float = Field(ge=0.0, le=1.0, description="감정 강도 0.0~1.0")


IntentType = Literal["casual", "공감_케어", "기록", "계획", "수정", "계획_승인", "정보", "안전경고", "fallback"]
RecordType = Literal["profile", "plan_check", "plan_delete"]
ModifyTarget = Literal["workout", "diet"]


class ProfileChange(BaseModel):
    field: str = Field(description="변경할 프로필 필드명 (예: weight, height)")
    value: str = Field(description="변경할 값")

class IntentOutput(BaseModel):
    """Flash-Lite가 반환하는 의도 분석 결과."""

    # ── 공통 속성 ────────────────────────────────────────────────────────────
    intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    emotion: EmotionOutput

    # ── 판단 기반 속성 ────────────────────────────────────────────────────────
    has_fact_change: bool = Field(default=False, description="프로필 변경 감지 여부")
    requires_past_memory: bool = Field(default=False, description="과거 에피소드 참조 필요 여부")

    # ── 의도별 조건부 속성 ────────────────────────────────────────────────────
    # 공감_케어
    should_save_episode: bool = Field(default=False)

    # 기록
    record_type: Optional[RecordType] = None
    profile_changes: Optional[list[ProfileChange]] = None
    is_today: Optional[bool] = None

    # 수정
    modify_target: Optional[ModifyTarget] = None

    # 계획 · 정보 · 수정
    search_targets: list[str] = Field(
        default_factory=list,
        description="검색 대상: vdb_memory, vdb_user_important, vdb_external, web",
    )
