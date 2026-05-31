"""Structured response schemas used with Gemini response_schema."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MemoryOperation(BaseModel):
    action: str = Field(description="One of ADD, UPDATE, DELETE, KEEP")
    fact_id: str = Field(default="", description="Existing fact id for update/delete")
    text: str = Field(default="", description="New or updated fact text")


class MemoryManagerResponse(BaseModel):
    has_changes: bool = Field(description="Whether memory updates are required")
    operations: list[MemoryOperation] = Field(default_factory=list)


class SearchEvalResponse(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="Search relevance score")
    reason: str = Field(default="", description="Short reason for the score")


class QueryRegenResponse(BaseModel):
    query: str = Field(description="A rewritten search query")


class SelfEvalResponse(BaseModel):
    passed: bool = Field(default=True, description="Whether the draft passes self-eval")
    reason: str = Field(default="", description="Short failure reason when not passed")


class AnswerValidationIssue(BaseModel):
    severity: Literal["critical", "warning"] = Field(
        default="warning",
        description="Use critical only for concrete safety/profile/domain conflicts.",
    )
    code: str = Field(default="", description="Stable short issue code")
    message: str = Field(default="", description="Brief issue explanation")
    retry: bool = Field(default=False, description="Whether another generation pass can fix this")


class AnswerValidationJudgeResponse(BaseModel):
    passed: bool = Field(default=True, description="Whether the answer passes semantic validation")
    issues: list[AnswerValidationIssue] = Field(default_factory=list)


class ExerciseItem(BaseModel):
    exercise_name: str = Field(description="Detailed exercise name")
    sets: Optional[int] = Field(default=None, description="Number of sets")
    duration_minutes: Optional[int] = Field(
        default=None,
        description="Duration in minutes for cardio exercises",
    )
    calories: int = Field(default=0, description="Estimated calories")


class PlanExtractItem(BaseModel):
    name: str = Field(description="Workout category or meal name")
    detail: str = Field(default="", description="Workout session or meal description")
    day: str = Field(default="", description="Date in YYYY-MM-DD format")
    ex_list: list[ExerciseItem] = Field(
        default_factory=list,
        description="Detailed exercise list for workout plans; leave empty for diet plans",
    )


PlanType = Literal["workout", "diet"]


class DraftResponse(BaseModel):
    """Structured output from the Draft node before persona styling."""

    core_message: str = Field(description="Primary answer or main decision")
    reason_points: list[str] = Field(
        default_factory=list,
        description="Short factual reasons that support the core message",
    )
    suggested_action: str = Field(
        default="",
        description="Concrete next action or practical application",
    )
    safety_notes: list[str] = Field(
        default_factory=list,
        description="Warnings or safety notes when needed",
    )
    approval_question: Optional[str] = Field(
        default=None,
        description="Question asking the user to approve a proposed plan change",
    )
    search_grounding_summary: str = Field(
        default="",
        description="Short summary of how search evidence was used",
    )
    proposed_plan: list[PlanExtractItem] = Field(
        default_factory=list,
        description="Structured plan items when the node is proposing a plan",
    )
    proposed_plan_type: Optional[PlanType] = Field(
        default=None,
        description='Plan type when proposed_plan exists: "workout" or "diet"',
    )


class PlanExtractResponse(BaseModel):
    has_plan: bool = Field(description="Whether a plan was extracted")
    plan_type: PlanType = Field(default="workout", description="workout or diet")
    items: list[PlanExtractItem] = Field(default_factory=list)


class PlanModifyResponse(BaseModel):
    has_changes: bool = Field(description="Whether the plan contains changes")
    plan_type: PlanType = Field(default="workout", description="workout or diet")
    items: list[PlanExtractItem] = Field(default_factory=list)
