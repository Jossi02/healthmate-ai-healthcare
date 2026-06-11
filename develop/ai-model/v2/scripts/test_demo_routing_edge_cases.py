"""Demo-critical routing edge case checks.

These cases focus on short natural Korean requests that are likely during a
live demo. They verify intent routing, target source, date resolution, and
commit behavior without requiring Gemini, Pinecone, WAS, or a running server.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.graph.nodes import fast_flow  # noqa: E402


def _route(message: str) -> dict[str, Any]:
    return fast_flow._route_message(message, {"active_proposal": None})  # noqa: SLF001


def _actions(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return list(contract.get("actions") or [])


def _dates(action: dict[str, Any]) -> list[str]:
    return list((action.get("target") or {}).get("dates") or [])


def _slots(action: dict[str, Any]) -> list[str]:
    return list((action.get("target") or {}).get("meal_slots") or [])


def _scope(action: dict[str, Any]) -> str | None:
    return (action.get("target") or {}).get("scope")


def _rolling_week_dates() -> list[str]:
    today = fast_flow._today()  # noqa: SLF001
    return [(today + timedelta(days=offset)).isoformat() for offset in range(7)]


def _this_weekday(weekday: int) -> str:
    today = fast_flow._today()  # noqa: SLF001
    return (today - timedelta(days=today.weekday()) + timedelta(days=weekday)).isoformat()


def _next_week_dates() -> list[str]:
    today = fast_flow._today()  # noqa: SLF001
    next_monday = today + timedelta(days=(7 - today.weekday()))
    return [(next_monday + timedelta(days=offset)).isoformat() for offset in range(7)]


def _next_weekday(weekday: int) -> str:
    return _next_week_dates()[weekday]


CASES: list[dict[str, Any]] = [
    {
        "label": "natural_saved_breakfast_modify",
        "message": "오늘 아침 현미죽으로 바꿔줘",
        "expect": {
            "operation": "plan.modify",
            "domain": "diet",
            "source": "saved_planner",
            "date_count": 1,
            "meal_slots": ["breakfast"],
            "commit": False,
        },
    },
    {
        "label": "calendar_saved_breakfast_commit",
        "message": "캘린더 플랜 오늘 아침을 현미죽으로 바꿔서 반영해줘",
        "expect": {
            "operation": "plan.modify",
            "domain": "diet",
            "source": "saved_planner",
            "date_count": 1,
            "meal_slots": ["breakfast"],
            "commit": True,
        },
    },
    {
        "label": "this_week_monday_single_date",
        "message": "이번 주 월요일 아침만 현미죽으로 바꿔서 반영해줘",
        "expect": {
            "operation": "plan.modify",
            "domain": "diet",
            "dates": [_this_weekday(0)],
            "meal_slots": ["breakfast"],
            "commit": True,
        },
    },
    {
        "label": "next_week_diet_range",
        "message": "다음 주 식단 짜줘",
        "expect": {
            "operation": "plan.create",
            "domain": "diet",
            "dates": _next_week_dates(),
            "commit": False,
        },
    },
    {
        "label": "next_week_monday_workout_single_date",
        "message": "다음 주 월요일 운동 짜줘",
        "expect": {
            "operation": "plan.create",
            "domain": "workout",
            "dates": [_next_weekday(0)],
            "commit": False,
        },
    },
    {
        "label": "next_week_saved_diet_modify_range",
        "message": "다음 주 식단 바꿔줘",
        "expect": {
            "operation": "plan.modify",
            "domain": "diet",
            "source": "saved_planner",
            "dates": _next_week_dates(),
            "commit": False,
        },
    },
    {
        "label": "next_week_dinner_commit_range",
        "message": "다음주 저녁만 두부구이로 변경해서 반영해줘",
        "expect": {
            "operation": "plan.modify",
            "domain": "diet",
            "source": "saved_planner",
            "dates": _next_week_dates(),
            "meal_slots": ["dinner"],
            "commit": True,
        },
    },
    {
        "label": "next_week_bundle_create_range",
        "message": "운동이랑 식단 둘 다 다음 주 짜줘",
        "expect": {
            "route_kind": "bundle",
            "action_count": 2,
            "domains": ["workout", "diet"],
            "dates": _next_week_dates(),
        },
    },
    {
        "label": "mixed_workout_delete_diet_modify",
        "message": "운동은 삭제하고 식단은 저녁만 두부구이로 바꿔서 반영해줘",
        "expect": {
            "route_kind": "bundle",
            "action_count": 2,
            "mixed": [
                {"operation": "plan.delete", "domain": "workout"},
                {"operation": "plan.modify", "domain": "diet", "meal_slots": ["dinner"], "commit": True},
            ],
        },
    },
    {
        "label": "mixed_diet_delete_workout_modify",
        "message": "식단은 삭제하고 운동은 스트레칭으로 바꿔줘",
        "expect": {
            "route_kind": "bundle",
            "action_count": 2,
            "mixed": [
                {"operation": "plan.modify", "domain": "workout", "workout_categories": ["stretching"], "commit": False},
                {"operation": "plan.delete", "domain": "diet"},
            ],
        },
    },
    {
        "label": "bare_week_all_delete_range",
        "message": "\uc77c\uc8fc\uc77c\uce58\ubaa8\ub450 \uc0ad\uc81c\ud574\uc918",
        "expect": {
            "operation": "plan.delete",
            "domain": "all",
            "dates": _rolling_week_dates(),
            "scope": "range",
            "commit": True,
        },
    },
    {
        "label": "this_week_all_delete_range",
        "message": "\uc774\ubc88 \uc8fc \ubaa8\ub450 \uc0ad\uc81c\ud574\uc918",
        "expect": {
            "operation": "plan.delete",
            "domain": "all",
            "dates": _rolling_week_dates(),
            "scope": "range",
            "commit": True,
        },
    },
]


def _check_case(case: dict[str, Any]) -> dict[str, Any]:
    contract = _route(case["message"])
    actions = _actions(contract)
    expect = case["expect"]
    failures: list[str] = []

    if expect.get("route_kind") and contract.get("route_kind") != expect["route_kind"]:
        failures.append(f"route_kind={contract.get('route_kind')} expected={expect['route_kind']}")
    if expect.get("action_count") is not None and len(actions) != expect["action_count"]:
        failures.append(f"action_count={len(actions)} expected={expect['action_count']}")
    if not actions:
        failures.append("missing_action")
        return {"label": case["label"], "passed": False, "failures": failures, "contract": contract}

    if "mixed" in expect:
        actual = [
            {
                "operation": action.get("operation"),
                "domain": action.get("domain"),
                "meal_slots": _slots(action),
                "workout_categories": list((action.get("target") or {}).get("workout_categories") or []),
                "commit": action.get("commit"),
            }
            for action in actions
        ]
        for index, expected_action in enumerate(expect["mixed"]):
            if index >= len(actual):
                failures.append(f"missing_mixed_action_{index}")
                continue
            for key, expected_value in expected_action.items():
                if actual[index].get(key) != expected_value:
                    failures.append(f"mixed_{index}_{key}={actual[index].get(key)} expected={expected_value}")
        return {"label": case["label"], "passed": not failures, "failures": failures, "contract": contract}

    if "domains" in expect:
        domains = [action.get("domain") for action in actions]
        if domains != expect["domains"]:
            failures.append(f"domains={domains} expected={expect['domains']}")
        for action in actions:
            if _dates(action) != expect["dates"]:
                failures.append(f"{action.get('domain')}_dates={_dates(action)} expected={expect['dates']}")
        return {"label": case["label"], "passed": not failures, "failures": failures, "contract": contract}

    action = actions[0]
    for key in ("operation", "domain", "source", "commit"):
        source_key = "target_source" if key == "source" else key
        if key in expect and action.get(source_key) != expect[key]:
            failures.append(f"{key}={action.get(source_key)} expected={expect[key]}")
    if "dates" in expect and _dates(action) != expect["dates"]:
        failures.append(f"dates={_dates(action)} expected={expect['dates']}")
    if "scope" in expect and _scope(action) != expect["scope"]:
        failures.append(f"scope={_scope(action)} expected={expect['scope']}")
    if "date_count" in expect and len(_dates(action)) != expect["date_count"]:
        failures.append(f"date_count={len(_dates(action))} expected={expect['date_count']}")
    if "meal_slots" in expect and _slots(action) != expect["meal_slots"]:
        failures.append(f"meal_slots={_slots(action)} expected={expect['meal_slots']}")

    return {"label": case["label"], "passed": not failures, "failures": failures, "contract": contract}


def main() -> None:
    results = [_check_case(case) for case in CASES]
    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
