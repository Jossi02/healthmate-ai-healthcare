from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
from fastapi import FastAPI

for key, value in {
    "GEMINI_API_KEY": "test-gemini",
    "ROUTER_API_KEY": "test-router",
    "PINECONE_API_KEY": "test-pinecone",
    "PINECONE_INDEX_NAME": "test-index",
    "WAS_BASE_URL": "http://was.test",
    "INTERNAL_API_KEY": "test-internal-key",
    "APP_ENV": "development",
}.items():
    os.environ.setdefault(key, value)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.checkpoint_filter import FilteringAsyncSqliteSaver
from app.core.exceptions import ExternalServiceError
from app.core.lifespan import _ensure_activity_table
from app.core.trace_store import TraceStore
from app.graph.builder import build_graph
from app.graph.deps import NodeDeps
from app.graph.nodes.intent import INTENT_INFO, INTENT_RECORD
from app.routers.chat import router as chat_router

MSG_CREATE_WORKOUT = "\uc624\ub298 \uc6b4\ub3d9 \uacc4\ud68d \uc9dc\uc918"
MSG_CREATE_MIXED_PLAN = "\uc624\ub298 \uc6b4\ub3d9 \uacc4\ud68d\uacfc \uc2dd\ub2e8 \uacc4\ud68d\uc744 \uac19\uc774 \uc9dc\uc918"
MSG_MODIFY_WORKOUT = "\uadf8\uac70 \uc880 \ub35c \ube61\uc138\uac8c \ubc14\uafd4\uc918"
MSG_INFO_REASON = "\uc65c \uadf8\ub807\uac8c \uc9f0\uc5b4?"
MSG_APPROVAL = "\uc88b\uc544 \uadf8\uac78\ub85c \uc9c4\ud589\ud574\uc918"
MSG_PLAN_DELETE = "\uc624\ub298 \uc6b4\ub3d9 \ud50c\ub79c \uc0ad\uc81c\ud574\uc918"
MSG_PLAN_DELETE_ALL = "\ud604\uc7ac \uce98\ub9b0\ub354\uc758 \ubaa8\ub4e0 \uc6b4\ub3d9/\uc2dd\ub2e8 \ub0b4\uc5ed\uc744 \uc0ad\uc81c\ud574\uc918"
MSG_CARE = "\uc624\ub298 \ub108\ubb34 \ud798\ub4e4\uace0 \uc9c0\ucce4\uc5b4"
MSG_SAFETY = "\ud638\ud761\uace4\ub780\uc774 \uc788\uace0 \uac00\uc2b4 \ud1b5\uc99d\uc774 \uc788\uc5b4"


class FakeProfileSync:
    def __init__(self) -> None:
        self._versions: dict[str, int] = {}

    async def get_profile_version(self, user_id: str) -> int:
        return self._versions.get(user_id, 0)

    def bump(self, user_id: str) -> None:
        self._versions[user_id] = self._versions.get(user_id, 0) + 1


class FakeEmbed:
    async def embed(self, text: str) -> list[float]:
        base = float(len(text.strip()) or 1)
        return [base, base / 10.0, base / 100.0]


class FakePinecone:
    def __init__(self) -> None:
        self.memory: dict[str, list[dict[str, Any]]] = {}
        self.important: dict[str, list[dict[str, Any]]] = {}

    async def search_memory(
        self,
        user_id: str,
        vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.memory.get(user_id, []))[:top_k]

    async def search_important(
        self,
        user_id: str,
        vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.important.get(user_id, []))[:top_k]

    async def search_external(
        self,
        vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        domain = _infer_external_domain(metadata_filter)
        if domain == "diet":
            texts = [
                "Balanced protein and fiber support satiety.",
                "Allergen ingredients should be replaced with safe alternatives.",
            ]
        else:
            texts = [
                "Early plans should prioritize sustainability over intensity.",
                "Pain or injury history should lower impact and volume.",
            ]
        return [
            {
                "id": f"external-{domain}-{idx}",
                "source": "external",
                "text": text,
                "score": 0.92 - idx * 0.05,
                "metadata": {"category": domain},
            }
            for idx, text in enumerate(texts[:top_k])
        ]

    async def upsert_memory(
        self,
        user_id: str,
        vector: list[float],
        text: str,
        emotion_label: str,
        intensity: float,
    ) -> None:
        self.memory.setdefault(user_id, []).append(
            {"id": f"mem-{len(self.memory.get(user_id, []))}", "text": text, "score": 0.7, "source": "memory"}
        )

    async def upsert_important(self, user_id: str, vector: list[float], text: str) -> None:
        self.important.setdefault(user_id, []).append(
            {"id": f"fact-{len(self.important.get(user_id, []))}", "text": text, "score": 0.8, "source": "important"}
        )

    async def delete_important(self, user_id: str, ids: list[str]) -> None:
        existing = self.important.get(user_id, [])
        self.important[user_id] = [item for item in existing if item.get("id") not in set(ids)]


class FakeWAS:
    def __init__(self, profile_sync: FakeProfileSync) -> None:
        self.profile_sync = profile_sync
        self.profiles: dict[str, dict[str, Any]] = {}
        self.today_plans: dict[str, list[dict[str, Any]]] = {}
        self.full_plans: dict[str, dict[str, dict[str, Any]]] = {}
        self.write_log: list[tuple[str, str, Any]] = []

    def _ensure_user(self, user_id: str) -> None:
        self.profiles.setdefault(
            user_id,
            {
                "selected_ai_persona": "default",
                "goal": "maintain",
                "allergies": [],
                "injury_history": [],
            },
        )
        self.today_plans.setdefault(
            user_id,
            [
                {"id": f"{user_id}-exercise-1", "name": "Upper Body", "type": "exercise", "completed": False},
                {"id": f"{user_id}-meal-1", "name": "Breakfast", "type": "meal", "completed": False},
            ],
        )
        self.full_plans.setdefault(
            user_id,
            {
                "workout": {"items": []},
                "diet": {"items": []},
            },
        )

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        self._ensure_user(user_id)
        return dict(self.profiles[user_id])

    async def get_today_plan(self, user_id: str) -> list[dict[str, Any]]:
        self._ensure_user(user_id)
        return [dict(item) for item in self.today_plans[user_id]]

    async def get_workout_plan_full(self, user_id: str) -> dict[str, Any]:
        self._ensure_user(user_id)
        return json.loads(json.dumps(self.full_plans[user_id]["workout"]))

    async def get_diet_plan_full(self, user_id: str) -> dict[str, Any]:
        self._ensure_user(user_id)
        return json.loads(json.dumps(self.full_plans[user_id]["diet"]))

    async def put_user_profile(self, user_id: str, payload: dict[str, Any]) -> None:
        self._ensure_user(user_id)
        clean_payload = {
            key: value
            for key, value in dict(payload or {}).items()
            if key not in {"_idempotency_key", "idempotency_key"}
        }
        self.profiles[user_id].update(clean_payload)
        self.profile_sync.bump(user_id)
        self.write_log.append(("profile", user_id, clean_payload))

    async def put_plan_check(
        self,
        user_id: str,
        item_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        self._ensure_user(user_id)
        updated: list[dict[str, Any]] = []
        for item in self.today_plans[user_id]:
            next_item = dict(item)
            if next_item.get("id") == item_id:
                next_item["completed"] = True
            updated.append(next_item)
        self.today_plans[user_id] = updated
        self.write_log.append(("plan_check", user_id, {"item_id": item_id, "idempotency_key": idempotency_key}))

    async def post_plan_create(self, user_id: str, payload: dict[str, Any]) -> None:
        await self._write_plan(user_id, payload, mode="create")

    async def put_plan_update(self, user_id: str, payload: dict[str, Any]) -> None:
        await self._write_plan(user_id, payload, mode="update")

    async def delete_plan(self, user_id: str, payload: dict[str, Any]) -> None:
        self._ensure_user(user_id)
        plan_type = str(payload.get("plan_type") or "all")
        target_dates = set(payload.get("target_dates") or [])

        def should_keep(item: dict[str, Any]) -> bool:
            item_day = str(item.get("day") or item.get("target_date") or "")
            if target_dates and item_day and item_day not in target_dates:
                return True
            item_type = str(item.get("type") or "")
            if plan_type == "workout":
                return item_type != "exercise"
            if plan_type == "diet":
                return item_type != "meal"
            return False

        for stored_plan_type in ("workout", "diet"):
            self.full_plans[user_id][stored_plan_type]["items"] = [
                item
                for item in self.full_plans[user_id][stored_plan_type]["items"]
                if should_keep(item)
            ]
        self.today_plans[user_id] = [item for item in self.today_plans[user_id] if should_keep(item)]
        self.write_log.append(("plan_delete", user_id, dict(payload)))

    async def _write_plan(self, user_id: str, payload: dict[str, Any], *, mode: str) -> None:
        self._ensure_user(user_id)
        plan_type = str(payload.get("plan_type") or "workout")
        items: list[dict[str, Any]] = []
        for index, item in enumerate(payload.get("items") or [], start=1):
            normalized = dict(item)
            normalized.setdefault("id", f"{user_id}-{plan_type}-{index}")
            normalized.setdefault("completed", False)
            normalized["type"] = "meal" if plan_type == "diet" else "exercise"
            items.append(normalized)
        self.full_plans[user_id][plan_type] = {"items": items}
        combined_items: list[dict[str, Any]] = []
        for stored_plan_type in ("workout", "diet"):
            for stored_item in self.full_plans[user_id][stored_plan_type]["items"]:
                combined_items.append(dict(stored_item))
        self.today_plans[user_id] = combined_items
        self.write_log.append((f"plan_{mode}", user_id, {"plan_type": plan_type, "count": len(items)}))


class FlakyWAS(FakeWAS):
    def __init__(
        self,
        profile_sync: FakeProfileSync,
        *,
        missing_profile_users: set[str] | None = None,
        missing_today_users: set[str] | None = None,
        failing_workout_full_users: set[str] | None = None,
    ) -> None:
        super().__init__(profile_sync)
        self.missing_profile_users = set(missing_profile_users or set())
        self.missing_today_users = set(missing_today_users or set())
        self.failing_workout_full_users = set(failing_workout_full_users or set())

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        if user_id in self.missing_profile_users:
            raise ExternalServiceError(service="WAS", message="HTTP 404", status_code=404)
        return await super().get_user_profile(user_id)

    async def get_today_plan(self, user_id: str) -> list[dict[str, Any]]:
        if user_id in self.missing_today_users:
            raise ExternalServiceError(service="WAS", message="HTTP 404", status_code=404)
        return await super().get_today_plan(user_id)

    async def get_workout_plan_full(self, user_id: str) -> dict[str, Any]:
        if user_id in self.failing_workout_full_users:
            raise ExternalServiceError(service="WAS", message="HTTP 500", status_code=500)
        return await super().get_workout_plan_full(user_id)


class FakeRouter:
    async def generate(self, *, system_prompt: str, user_content: str, response_schema):  # noqa: ANN001
        schema_name = getattr(response_schema, "__name__", "")
        if schema_name == "IntentOutput":
            return json.dumps(self._intent_output(user_content), ensure_ascii=False)
        if schema_name == "PlanConfirmationDecision":
            return json.dumps(self._plan_confirmation(user_content), ensure_ascii=False)
        if schema_name == "SearchEvalResponse":
            return json.dumps({"score": 0.95, "reason": "fake_eval"}, ensure_ascii=False)
        if schema_name == "QueryRegenResponse":
            return json.dumps({"query": _extract_after_marker(user_content, "\uc6d0\ub798 \uc9c8\ubb38:") or "search query"}, ensure_ascii=False)
        if schema_name == "DraftResponse":
            return json.dumps(self._draft_response(user_content), ensure_ascii=False)
        if schema_name == "SelfEvalResponse":
            return json.dumps({"passed": True, "reason": ""}, ensure_ascii=False)
        if schema_name == "AnswerValidationJudgeResponse":
            return json.dumps({"passed": True, "issues": []}, ensure_ascii=False)
        if schema_name == "PersonaResponse":
            return json.dumps({"response": self._persona_response(user_content)}, ensure_ascii=False)
        if schema_name == "MemoryManagerResponse":
            return json.dumps({"has_changes": False, "operations": []}, ensure_ascii=False)
        raise RuntimeError(f"Unsupported fake schema: {schema_name}")

    async def search_web(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "id": f"web-{idx}",
                "source": "web",
                "text": f"web evidence {idx + 1}: {query}",
                "score": 0.75 - idx * 0.05,
            }
            for idx in range(min(max_results, 2))
        ]

    def _intent_output(self, user_content: str) -> dict[str, Any]:
        message = _extract_resolved_message(user_content).lower()
        emotion_label = "\uc911\ub9bd"
        emotion_intensity = 0.0
        if any(token in message for token in ("\uc9c0\uccd0", "\ud798\ub4e4", "\ubd88\uc548", "\uc6b8\uc6b8", "\uc678\ub86c", "\uac71\uc815", "\uc2a4\ud2b8\ub808\uc2a4")):
            emotion_label = "\ubd88\uc548"
            emotion_intensity = 0.76

        if any(token in message for token in ("\uc624\ub298 \uc6b4\ub3d9 \uccb4\ud06c", "\uc6b4\ub3d9 \uccb4\ud06c\ud588\uc5b4", "\uc6b4\ub3d9 \uc644\ub8cc")):
            return {
                "intent": INTENT_RECORD,
                "confidence": 0.94,
                "emotion": {"label": emotion_label, "intensity": emotion_intensity},
                "has_fact_change": False,
                "requires_past_memory": False,
                "should_save_episode": False,
                "record_type": "plan_check",
                "profile_changes": None,
                "is_today": True,
                "modify_target": None,
                "search_targets": [],
            }

        if any(token in message for token in ("\uc0ad\uc81c", "\uc9c0\uc6cc", "\uc81c\uac70", "\uc5c6\uc560", "\ube44\uc6cc", "\ucd08\uae30\ud654", "delete", "remove", "clear")) and any(
            token in message for token in ("\ud50c\ub79c", "\uacc4\ud68d", "\uce98\ub9b0\ub354", "\ub0b4\uc5ed", "\ub0b4\uc6a9", "\uc77c\uc815", "\uc6b4\ub3d9", "\uc2dd\ub2e8")
        ):
            return {
                "intent": INTENT_RECORD,
                "confidence": 0.95,
                "emotion": {"label": emotion_label, "intensity": emotion_intensity},
                "has_fact_change": False,
                "requires_past_memory": False,
                "should_save_episode": False,
                "record_type": "plan_delete",
                "profile_changes": None,
                "is_today": True,
                "modify_target": "workout" if "\uc6b4\ub3d9" in message and "\uc2dd\ub2e8" not in message else None,
                "search_targets": [],
            }

        if any(token in message for token in ("\uc65c", "\ubb50\uc57c", "\uc5bc\ub9c8\ub098", "\uad81\uae08", "\ub300\uc2e0 \ubb50")):
            return {
                "intent": INTENT_INFO,
                "confidence": 0.91,
                "emotion": {"label": emotion_label, "intensity": emotion_intensity},
                "has_fact_change": False,
                "requires_past_memory": False,
                "should_save_episode": False,
                "record_type": None,
                "profile_changes": None,
                "is_today": None,
                "modify_target": None,
                "search_targets": ["vdb_external"],
            }

        if any(token in message for token in ("\uc678\ub85c\uc6cc", "\uc678\ub86d", "\uc8fc\ub9d0 \uc798 \ubcf4\ub0b4", "\ubcc4\uc77c \uc5c6\uc9c0")):
            return {
                "intent": "casual",
                "confidence": 0.82,
                "emotion": {"label": emotion_label, "intensity": emotion_intensity},
                "has_fact_change": False,
                "requires_past_memory": False,
                "should_save_episode": False,
                "record_type": None,
                "profile_changes": None,
                "is_today": None,
                "modify_target": None,
                "search_targets": [],
            }

        return {
            "intent": "fallback",
            "confidence": 0.3,
            "emotion": {"label": emotion_label, "intensity": emotion_intensity},
            "has_fact_change": False,
            "requires_past_memory": False,
            "should_save_episode": False,
            "record_type": None,
            "profile_changes": None,
            "is_today": None,
            "modify_target": None,
            "search_targets": [],
        }

    def _plan_confirmation(self, user_content: str) -> dict[str, Any]:
        message = _extract_after_marker(user_content, "[User Message]").lower()
        approved = (
            any(token in message for token in ("\uc88b\uc544", "\uc9c4\ud589", "\uc801\uc6a9", "\ubc18\uc601", "\uadf8\ub300\ub85c", "\uc751", "\ub124", "\uc624\ucf00\uc774", "\ud655\uc778"))
            and not any(token in message for token in ("\ubc14\uafd4", "\uc218\uc815", "\ubcc0\uacbd", "\uc870\uc815", "\ub367", "\ube7c", "\ucd94\uac00", "\uc81c\uc678"))
        )
        return {"approved": approved, "confidence": 0.97 if approved else 0.2, "reason": "fake_confirmation"}

    def _draft_response(self, user_content: str) -> dict[str, Any]:
        message = _extract_generate_message(user_content).lower()
        if any(token in message for token in ("\uccb4\uc911", "\ubab8\ubb34\uac8c", "\uc54c\ub808\ub974\uae30", "\ubaa9\ud45c")):
            return {
                "core_message": "Profile updated.",
                "reason_points": ["The latest user-provided profile field was applied."],
                "suggested_action": "If you want, I can update another profile field too.",
                "safety_notes": [],
                "approval_question": None,
                "search_grounding_summary": "",
                "proposed_plan": [],
                "proposed_plan_type": None,
            }

        if any(token in message for token in ("\uc65c", "\ubb50\uc57c", "\uc5bc\ub9c8\ub098", "\ub300\uc2e0 \ubb50", "\uad81\uae08")):
            return {
                "core_message": "Here is the main reason behind that recommendation.",
                "reason_points": ["I combined the user context with policy evidence."],
                "suggested_action": "If you want, I can explain the reasoning in more detail.",
                "safety_notes": [],
                "approval_question": None,
                "search_grounding_summary": "I summarized the search evidence into the answer.",
                "proposed_plan": [],
                "proposed_plan_type": None,
            }

        if any(token in message for token in ("\uc678\ub85c\uc6cc", "\uc678\ub86d", "\uc9c0\uccd0", "\ud798\ub4e4", "\ubd88\uc548", "\uc6b8\uc6b8")):
            return {
                "core_message": "You do not need to push hard today.",
                "reason_points": ["Lowering the burden fits your current emotional state better."],
                "suggested_action": "If you want, I can switch to a gentler plan.",
                "safety_notes": [],
                "approval_question": None,
                "search_grounding_summary": "",
                "proposed_plan": [],
                "proposed_plan_type": None,
            }

        if "\uc6b4\ub3d9" in message and "\uc2dd\ub2e8" in message:
            return {
                "core_message": "Here is a combined workout and meal plan.",
                "reason_points": ["I balanced movement, recovery, and meal timing together."],
                "suggested_action": "If you want, tell me whether to proceed with this combined plan.",
                "safety_notes": [],
                "approval_question": "Should I proceed with this combined plan?",
                "search_grounding_summary": "I applied both workout and diet guidance.",
                "proposed_plan": [
                    {
                        "name": "Morning Workout",
                        "detail": "Short strength and cardio session",
                        "day": "2026-04-16",
                        "ex_list": [
                            {"exercise_name": "Leg Press", "sets": 3, "calories": 80},
                            {"exercise_name": "Treadmill Walk", "duration_minutes": 20, "calories": 90},
                        ],
                    },
                    {
                        "name": "Breakfast",
                        "detail": "Greek yogurt and berries",
                        "day": "2026-04-16",
                        "ex_list": [{"exercise_name": "Greek yogurt and berries", "sets": 3, "calories": 0}],
                    },
                    {
                        "name": "Lunch",
                        "detail": "Chicken salad bowl",
                        "day": "2026-04-16",
                        "ex_list": [{"exercise_name": "Chicken salad bowl", "sets": 3, "calories": 0}],
                    },
                    {
                        "name": "Dinner",
                        "detail": "Salmon and vegetables",
                        "day": "2026-04-16",
                        "ex_list": [{"exercise_name": "Salmon and vegetables", "sets": 3, "calories": 0}],
                    },
                ],
                "proposed_plan_type": "workout",
            }

        if any(token in message for token in ("\uc2dd\ub2e8", "\uce7c\ub85c\ub9ac", "\uc2dd\uc0ac")):
            is_modify = any(token in message for token in ("\uc218\uc815", "\ubc14\uafd4", "\uc870\uc815", "\uc81c\uc678", "\ub367"))
            plan_items = [
                {"name": "Breakfast", "detail": "Greek yogurt and fruit", "day": "2026-04-16", "ex_list": []},
                {"name": "Dinner", "detail": "Chicken breast and vegetables", "day": "2026-04-16", "ex_list": []},
            ]
            if is_modify:
                plan_items[0]["detail"] = "Oatmeal and banana"
            return {
                "core_message": "Here is a diet plan." if not is_modify else "I adjusted the diet plan to make it lighter.",
                "reason_points": ["I considered both the user goal and food constraints."],
                "suggested_action": "If you want, tell me whether to proceed with this plan.",
                "safety_notes": [],
                "approval_question": "Should I proceed with this diet plan?",
                "search_grounding_summary": "I applied diet guidance and user constraints.",
                "proposed_plan": plan_items,
                "proposed_plan_type": "diet",
            }

        is_modify = any(token in message for token in ("\uc218\uc815", "\ubc14\uafd4", "\uc870\uc815", "\ub367", "\uc57d\ud558\uac8c"))
        exercise_sets = 3 if not is_modify else 2
        plan_items = [
            {
                "name": "Lower Body Session",
                "detail": "Lower-impact lower body routine",
                "day": "2026-04-16",
                "ex_list": [
                    {"exercise_name": "Leg Press", "sets": exercise_sets, "calories": 80},
                    {"exercise_name": "Bridge", "sets": exercise_sets, "calories": 40},
                ],
            },
            {
                "name": "Cardio",
                "detail": "Low-intensity walking",
                "day": "2026-04-16",
                "ex_list": [{"exercise_name": "Treadmill Walk", "duration_minutes": 20, "calories": 90}],
            },
        ]
        return {
            "core_message": "Here is a workout plan." if not is_modify else "I prepared a lower-intensity workout update.",
            "reason_points": ["I prioritized sustainability and safety."],
            "suggested_action": "If you want, tell me whether to proceed with this plan.",
            "safety_notes": [],
            "approval_question": "Should I proceed with this workout plan?",
            "search_grounding_summary": "I applied workout guidance and user constraints.",
            "proposed_plan": plan_items,
            "proposed_plan_type": "workout",
        }

    def _persona_response(self, user_content: str) -> str:
        marker = "[Structured Draft]\n"
        payload_text = user_content.split(marker, 1)[1] if marker in user_content else "{}"
        payload = json.loads(payload_text)
        lines: list[str] = []
        for key in ("core_message", "plan_preview", "suggested_action", "approval_question", "search_grounding_summary"):
            value = str(payload.get(key) or "").strip()
            if value:
                lines.append(value)
        lines.extend(str(item).strip() for item in payload.get("safety_notes") or [] if str(item).strip())
        return "\n".join(lines) if lines else "Response ready."


def _infer_external_domain(metadata_filter: dict[str, Any] | None) -> str:
    if not metadata_filter:
        return "workout"
    serialized = json.dumps(metadata_filter, ensure_ascii=False)
    if "diet" in serialized or "nutrition" in serialized:
        return "diet"
    return "workout"


def _extract_resolved_message(user_content: str) -> str:
    if "[Resolved User Message]" in user_content:
        tail = user_content.split("[Resolved User Message]", 1)[1].strip()
        return tail.splitlines()[0].strip()
    if "[Original User Message]" in user_content:
        tail = user_content.split("[Original User Message]", 1)[1].strip()
        return tail.splitlines()[0].strip()
    return user_content.strip().splitlines()[-1].strip()


def _extract_after_marker(content: str, marker: str) -> str:
    if marker not in content:
        return ""
    tail = content.split(marker, 1)[1].strip()
    return tail.splitlines()[0].strip() if tail else ""


def _extract_generate_message(user_content: str) -> str:
    for marker in ("[\ud604\uc7ac \uc9c8\ubb38]", "[Current Recall Question]", "[User Message]"):
        if marker in user_content:
            tail = user_content.split(marker, 1)[1].strip()
            return tail.splitlines()[0].strip()
    return user_content.strip().splitlines()[-1].strip()


async def build_test_stack(
    fake_was: FakeWAS | None = None,
    fake_router: FakeRouter | None = None,
) -> tuple[FastAPI, Any, NodeDeps, FakeWAS, FilteringAsyncSqliteSaver]:
    profile_sync = fake_was.profile_sync if fake_was is not None else FakeProfileSync()
    fake_was = fake_was or FakeWAS(profile_sync)
    fake_router = fake_router or FakeRouter()
    deps = NodeDeps(
        gemini=fake_router,
        router=fake_router,
        was=fake_was,
        pinecone=FakePinecone(),
        embed=FakeEmbed(),
        profile_sync=profile_sync,
        trace=TraceStore(),
    )

    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "checkpoints.sqlite"
    conn = await aiosqlite.connect(str(db_path))
    checkpointer = FilteringAsyncSqliteSaver(conn)
    await checkpointer.setup()
    await _ensure_activity_table(str(db_path))
    graph = build_graph(deps, checkpointer=checkpointer)

    app = FastAPI()
    app.include_router(chat_router)
    app.state.graph = graph
    app.state.deps = deps
    app.state.trace_store = deps.trace
    app.state._temp_dir = temp_dir
    app.state._checkpointer = checkpointer
    app.state.checkpoint_db_path = str(db_path)
    return app, graph, deps, fake_was, checkpointer


async def run_request(
    client: httpx.AsyncClient,
    user_id: str,
    message: str,
    session_id: str | None = None,
    profile_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "user_id": user_id,
        "user_message": message,
        "session_id": session_id,
        "user_profile_override": profile_override
        if profile_override is not None
        else {"selected_ai_persona": "default", "goal": "maintain"},
    }
    response = await client.post(
        "/chat",
        json=payload,
        headers={"x-api-key": os.environ["INTERNAL_API_KEY"]},
    )
    body = response.json()
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {body}")
    return body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> None:
    app, _graph, deps, fake_was, checkpointer = await build_test_stack()
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            flow_user = f"e2e-flow-{uuid.uuid4().hex[:6]}"
            bundle_user = f"e2e-bundle-{uuid.uuid4().hex[:6]}"
            delete_user = f"e2e-delete-{uuid.uuid4().hex[:6]}"
            delete_all_user = f"e2e-delete-all-{uuid.uuid4().hex[:6]}"
            care_user = f"e2e-care-{uuid.uuid4().hex[:6]}"
            safety_user = f"e2e-safety-{uuid.uuid4().hex[:6]}"

            create = await run_request(client, flow_user, MSG_CREATE_WORKOUT)
            session_id = create["session_id"]
            create_debug = create["debug_state"]
            create_items = create_debug["proposed_plan"] or []
            create_trace = deps.trace.get_trace(create_debug["trace_id"])
            require(create_trace and create_trace.get("quality"), "chat trace should include quality report")
            require(create_debug["action_intent"] == "create", "create action_intent mismatch")
            require(create_debug["domain"] == "workout", "create domain mismatch")
            require(create_items and create_items[0].get("ex_list"), "create proposal missing workout items")
            require(create_debug["validation_report"]["passed"] is True, "create proposal should pass fast validation")
            first_exercise = str(create_items[0]["ex_list"][0]["exercise_name"])
            require(first_exercise in create["response"], "create response should preview the generated workout")

            modify = await run_request(client, flow_user, MSG_MODIFY_WORKOUT, session_id=session_id)
            modify_debug = modify["debug_state"]
            modify_items = modify_debug["proposed_plan"] or []
            require(modify_debug["action_intent"] == "modify", "modify action_intent mismatch")
            require(
                modify_debug["proposed_plan_action"] == "create",
                "modifying an unsaved proposal should preserve create write mode",
            )
            require(modify_debug["validation_report"]["passed"] is True, "modified proposal should pass fast validation")
            require(modify_items and modify_items[0].get("ex_list"), "modify proposal missing workout items")
            modified_exercise = str(modify_items[0]["ex_list"][0]["exercise_name"])
            require(modified_exercise in modify["response"], "modify response should preview the current proposal")

            info = await run_request(client, flow_user, MSG_INFO_REASON, session_id=session_id)
            info_debug = info["debug_state"]
            require(info_debug["action_intent"] == "info", "info action_intent mismatch")
            require(info_debug["domain"] == "workout", "info domain mismatch")
            require(bool(info["response"]), "info response should not be empty")
            require(info_debug["proposed_plan_count"] >= 1, "info turn should retain the active proposal")

            approval = await run_request(client, flow_user, MSG_APPROVAL, session_id=session_id)
            approval_debug = approval["debug_state"]
            require(approval_debug["action_intent"] == "approval", "approval action_intent mismatch")
            require(bool(approval.get("plan_sync_applied")), "approval should trigger synchronous WAS write")
            require(
                any(entry[0] == "plan_create" and entry[1] == flow_user for entry in fake_was.write_log),
                "approval should create the previously unsaved plan",
            )
            require(approval_debug["active_proposal"] is None, "active proposal should clear after approval")

            bundle = await run_request(client, bundle_user, MSG_CREATE_MIXED_PLAN)
            bundle_session_id = bundle["session_id"]
            bundle_debug = bundle["debug_state"]
            bundle_items = bundle_debug["proposed_plan"] or []
            bundle_workouts = [item for item in bundle_items if item.get("plan_type") == "workout"]
            bundle_meals = [item for item in bundle_items if item.get("plan_type") == "diet"]
            require(bundle_debug["action_intent"] == "create", "bundle create action_intent mismatch")
            require(bundle_debug["proposed_plan_type"] == "bundle", "mixed request should create a bundle proposal")
            require(bundle_debug["needs_clarification"] is False, "explicit mixed request should not clarify")
            require(len(bundle_workouts) == 1, "one-day bundle should include one workout item")
            require(
                {item.get("name") for item in bundle_meals} == {"Breakfast", "Lunch", "Dinner"},
                "one-day bundle should include all three meal slots",
            )

            bundle_approval = await run_request(client, bundle_user, MSG_APPROVAL, session_id=bundle_session_id)
            require(bool(bundle_approval.get("plan_sync_applied")), "bundle approval should write both plan domains")
            require(fake_was.full_plans[bundle_user]["workout"]["items"], "bundle approval should write workout items")
            require(fake_was.full_plans[bundle_user]["diet"]["items"], "bundle approval should write diet items")

            plan_delete = await run_request(client, delete_user, MSG_PLAN_DELETE)
            delete_debug = plan_delete["debug_state"]
            require(delete_debug["action_intent"] == "record", "plan_delete action_intent mismatch")
            require(delete_debug["record_type"] == "plan_delete", "plan_delete record_type mismatch")
            require(bool(plan_delete.get("plan_sync_applied")), "plan_delete should trigger synchronous WAS write")
            require(
                not any(item.get("type") == "exercise" for item in fake_was.today_plans[delete_user]),
                "today exercise items should be deleted",
            )

            plan_delete_all = await run_request(client, delete_all_user, MSG_PLAN_DELETE_ALL)
            delete_all_debug = plan_delete_all["debug_state"]
            delete_all_payload = next(
                payload
                for write_type, user_id, payload in reversed(fake_was.write_log)
                if write_type == "plan_delete" and user_id == delete_all_user
            )
            require(delete_all_debug["action_intent"] == "record", "plan_delete_all action_intent mismatch")
            require(delete_all_debug["record_type"] == "plan_delete", "plan_delete_all record_type mismatch")
            require(bool(plan_delete_all.get("plan_sync_applied")), "plan_delete_all should trigger synchronous WAS write")
            require(delete_all_payload.get("target_scope") == "all", "plan_delete_all should use full calendar scope")
            require(delete_all_payload.get("target_dates") == [], "plan_delete_all should not send today's date")
            require(fake_was.today_plans[delete_all_user] == [], "plan_delete_all should clear all current plan items")

            care = await run_request(client, care_user, MSG_CARE)
            care_debug = care["debug_state"]
            require(care_debug["action_intent"] == "care", "care action_intent mismatch")
            require(care_debug["support_mode"] == "care", "care support_mode mismatch")

            safety = await run_request(client, safety_user, MSG_SAFETY)
            safety_debug = safety["debug_state"]
            require(safety_debug["action_intent"] == "safety", "safety action_intent mismatch")
            require(bool(safety["response"]), "safety response should not be empty")

            print("[e2e] current fast-flow contract passed")
            print(f"  create session_id={session_id}")
            print(f"  approval plan_sync_applied={approval.get('plan_sync_applied')}")
            print(f"  bundle workout={len(bundle_workouts)} diet={len(bundle_meals)}")
            print(f"  plan_delete remaining={len(fake_was.today_plans[delete_user])}")
            print(f"  plan_delete_all scope={delete_all_payload.get('target_scope')} remaining={len(fake_was.today_plans[delete_all_user])}")
    finally:
        await checkpointer.conn.close()
        app.state._temp_dir.cleanup()

    await run_resilience_smoke()
    await run_partial_profile_override_smoke()
    await run_cross_user_session_isolation_smoke()


async def run_resilience_smoke() -> None:
    missing_user = f"e2e-missing-{uuid.uuid4().hex[:6]}"
    flaky_sync = FakeProfileSync()
    flaky_was = FlakyWAS(
        flaky_sync,
        missing_profile_users={missing_user},
        missing_today_users={missing_user},
        failing_workout_full_users={missing_user},
    )
    app, _graph, _deps, _, checkpointer = await build_test_stack(fake_was=flaky_was)
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await run_request(client, missing_user, MSG_CREATE_WORKOUT)
            require(create["debug_state"]["action_intent"] == "create", "missing-profile create should still succeed")
            require(bool(create["response"]), "missing-profile create response should not be empty")

            session_id = create["session_id"]
            modify = await run_request(client, missing_user, MSG_MODIFY_WORKOUT, session_id=session_id)
            modify_debug = modify["debug_state"]
            modify_items = modify_debug["proposed_plan"] or []
            require(modify_debug["action_intent"] == "modify", "modify should still succeed when full plan load fails")
            require(modify_items and modify_items[0].get("ex_list"), "resilient modify should retain workout items")
            first_exercise = str(modify_items[0]["ex_list"][0]["exercise_name"])
            require(first_exercise in modify["response"], "resilient modify should preview its workout proposal")

            print("[e2e-resilience] 2/2 passed")
    finally:
        await checkpointer.conn.close()
        app.state._temp_dir.cleanup()


async def run_partial_profile_override_smoke() -> None:
    profile_user = f"e2e-partial-profile-{uuid.uuid4().hex[:6]}"
    app, graph, deps, fake_was, checkpointer = await build_test_stack()
    fake_was.profiles[profile_user] = {
        "selected_ai_persona": "default",
        "goal": "muscle_gain",
        "allergies": ["milk"],
        "injury_history": ["knee pain"],
        "activity_level": "beginner",
    }
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first = await run_request(client, profile_user, MSG_CARE, profile_override={"selected_ai_persona": "default"})
            session_id = first["session_id"]
            second = await run_request(
                client,
                profile_user,
                MSG_CREATE_WORKOUT,
                session_id=session_id,
                profile_override={"selected_ai_persona": "cheer_sis"},
            )
            summary = second["debug_state"]["profile_signal_summary"]
            require(summary.get("selected_ai_persona") == "cheer_sis", "partial override should update persona")
            require(summary.get("goal") == "muscle_gain", "partial override should preserve saved goal")
            require(summary.get("allergies") == ["milk"], "partial override should preserve allergies")
            require(summary.get("injury_history") == ["knee pain"], "partial override should preserve injuries")
            print("[e2e-partial-profile-override] 1/1 passed")
    finally:
        await checkpointer.conn.close()
        app.state._temp_dir.cleanup()


async def run_cross_user_session_isolation_smoke() -> None:
    app, _graph, _deps, fake_was, checkpointer = await build_test_stack()
    transport = httpx.ASGITransport(app=app)
    shared_session_id = f"shared-public-{uuid.uuid4().hex[:8]}"
    user_a = f"tenant-a-{uuid.uuid4().hex[:6]}"
    user_b = f"tenant-b-{uuid.uuid4().hex[:6]}"

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await run_request(client, user_a, MSG_CREATE_WORKOUT, session_id=shared_session_id)
            require(created["session_id"] == shared_session_id, "public session id should remain unchanged")
            require(created["debug_state"]["active_proposal"], "user A should own an active proposal")

            user_b_approval = await run_request(client, user_b, MSG_APPROVAL, session_id=shared_session_id)
            require(user_b_approval["session_id"] == shared_session_id, "user B should receive the public session id")
            require(not user_b_approval.get("plan_sync_applied"), "user B must not approve user A's proposal")
            require(
                user_b_approval["debug_state"]["proposed_plan_count"] == 0,
                "user B must not inherit user A's checkpoint state",
            )
            require(
                not any(entry[1] == user_b for entry in fake_was.write_log),
                "user B must not receive a cross-user plan write",
            )

            user_a_approval = await run_request(client, user_a, MSG_APPROVAL, session_id=shared_session_id)
            require(user_a_approval.get("plan_sync_applied") is True, "user A should still approve its own proposal")
            require(fake_was.full_plans[user_a]["workout"]["items"], "user A plan should be written")
            require(not fake_was.full_plans[user_b]["workout"]["items"], "user B workout plan should remain empty")
            require(not fake_was.full_plans[user_b]["diet"]["items"], "user B diet plan should remain empty")
            print("[e2e-tenant-session] A/B checkpoint isolation passed")
    finally:
        await checkpointer.conn.close()
        app.state._temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
