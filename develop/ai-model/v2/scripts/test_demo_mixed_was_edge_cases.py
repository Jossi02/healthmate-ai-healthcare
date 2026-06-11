"""Demo-critical mixed action WAS write checks."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from test_fast_plan_flow_suite import (  # noqa: E402
    FakeDeps,
    FakeWAS,
    apply_writes_if_needed,
    initial_state,
    run_fast_nodes,
)


TODAY = "2026-06-12"


def _seed_was() -> FakeWAS:
    was = FakeWAS()
    was.workout_items = [
        {
            "plan_type": "workout",
            "name": "Cardio",
            "detail": "walk 20min",
            "day": TODAY,
            "ex_list": [{"exercise_name": "walk", "sets": 1}],
        }
    ]
    was.diet_items = [
        {"plan_type": "diet", "name": "Breakfast", "detail": "brown rice, egg", "day": TODAY, "calories": 450},
        {"plan_type": "diet", "name": "Lunch", "detail": "chicken salad", "day": TODAY, "calories": 520},
        {"plan_type": "diet", "name": "Dinner", "detail": "salmon, vegetables", "day": TODAY, "calories": 500},
    ]
    return was


async def _run(message: str) -> tuple[dict[str, Any], FakeWAS, dict[str, Any]]:
    was = _seed_was()
    deps = FakeDeps(was)
    profile = {
        "profile_id": "demo_mixed",
        "selected_ai_persona": "cheer_sis",
        "activity_level": "beginner",
        "goal": "health",
    }
    state = initial_state(profile, message, None, 1)
    await run_fast_nodes(state, deps)
    before_write_count = len(state.get("proposed_plan") or [])
    before_write_type = state.get("proposed_plan_type")
    write_result = await apply_writes_if_needed(state, deps)
    state["before_write_count"] = before_write_count
    state["before_write_type"] = before_write_type
    return state, was, write_result


async def main() -> None:
    state, was, write = await _run("운동은 삭제하고 식단은 저녁만 두부구이로 바꿔서 반영해줘")
    failures: list[str] = []

    if state.get("action_intent") != "approval":
        failures.append(f"action_intent={state.get('action_intent')}")
    if state.get("record_type") != "plan_delete":
        failures.append(f"record_type={state.get('record_type')}")
    if state.get("before_write_type") != "bundle":
        failures.append(f"before_write_type={state.get('before_write_type')}")
    if state.get("before_write_count") != 3:
        failures.append(f"before_write_count={state.get('before_write_count')}")
    if write.get("write_succeeded") is not True:
        failures.append(f"write_succeeded={write.get('write_succeeded')}")
    if was.workout_items:
        failures.append("workout_not_deleted")
    if len(was.diet_items) != 3:
        failures.append(f"diet_count={len(was.diet_items)}")

    meals = {item["name"]: item for item in was.diet_items}
    if meals.get("Breakfast", {}).get("detail") != "brown rice, egg":
        failures.append("breakfast_changed")
    if meals.get("Lunch", {}).get("detail") != "chicken salad":
        failures.append("lunch_changed")
    if "두부구이" not in str(meals.get("Dinner", {}).get("detail") or ""):
        failures.append("dinner_not_modified")

    if failures:
        print({"ok": False, "failures": failures, "write": write, "state": state, "was": was.__dict__})
        raise SystemExit(1)

    print({"ok": True, "write": write, "workout_count": len(was.workout_items), "diet_count": len(was.diet_items)})


if __name__ == "__main__":
    asyncio.run(main())
