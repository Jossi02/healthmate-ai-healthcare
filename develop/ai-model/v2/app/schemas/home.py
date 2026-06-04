"""Schemas for home-tab recommendation requests and responses."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


HomeRecommendationScope = Literal["all", "workout", "diet"]


class HomeRecommendationRecentHistory(BaseModel):
    workout: dict[str, list[str]] = Field(default_factory=dict)
    diet: dict[str, list[str]] = Field(default_factory=dict)


class HomeRecommendationRequest(BaseModel):
    user_id: str
    type: HomeRecommendationScope = "all"
    recent_recommendations: HomeRecommendationRecentHistory | None = None


class WorkoutRecommendationItem(BaseModel):
    exercise_name: str = Field(description="Recommended exercise name")
    summary: str = Field(default="", description="Short display summary")
    sets: Optional[int] = Field(default=None, description="Target sets when not cardio")
    duration_minutes: Optional[int] = Field(
        default=None,
        description="Target duration for cardio recommendations",
    )
    calories: int = Field(default=0, description="Estimated calories")


class DietRecommendationItem(BaseModel):
    food_name: str = Field(description="Recommended meal name")
    summary: str = Field(default="", description="Short display summary")
    calories: int = Field(default=0, description="Estimated calories")


class WorkoutRecommendationSlots(BaseModel):
    upper_body: Optional[WorkoutRecommendationItem] = None
    lower_body: Optional[WorkoutRecommendationItem] = None
    cardio: Optional[WorkoutRecommendationItem] = None
    stretching: Optional[WorkoutRecommendationItem] = None


class DietRecommendationSlots(BaseModel):
    breakfast: Optional[DietRecommendationItem] = None
    lunch: Optional[DietRecommendationItem] = None
    dinner: Optional[DietRecommendationItem] = None


class HomeRecommendationResponse(BaseModel):
    date: str
    scope: HomeRecommendationScope = "all"
    workout: WorkoutRecommendationSlots = Field(default_factory=WorkoutRecommendationSlots)
    diet: DietRecommendationSlots = Field(default_factory=DietRecommendationSlots)
    quality_flags: dict[str, Any] = Field(default_factory=dict)
