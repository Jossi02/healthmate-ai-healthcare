from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import statistics
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

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
CHECKPOINT_DB_PATH = ROOT / "data" / "persona_field_6x5x20_checkpoints.sqlite"
os.environ["CHECKPOINT_DB_PATH"] = str(CHECKPOINT_DB_PATH)

from app.core import config as app_config  # noqa: E402
from app.core.persona_style import PERSONA_STYLE_SPECS  # noqa: E402
from scripts.test_chat_e2e import build_test_stack  # noqa: E402
from scripts.test_persona_20x20_suite import (  # noqa: E402
    PersonaContractRouter,
    SAFETY_MARKERS,
    _contains_any,
    _flatten_text,
    _profile_markers,
    build_turns,
    run_request,
)

app_config.Settings.model_config = {"env_file": None}
app_config.get_settings.cache_clear()

DATA_PATH = ROOT / "data" / "persona_field_6x5x20_dataset.json"
REPORT_JSON_PATH = ROOT / "docs" / "quality" / "persona_field_6x5x20_report.json"
REPORT_MD_PATH = ROOT / "docs" / "quality" / "persona_field_6x5x20_report.md"
REQUEST_TIMEOUT_SECONDS = 20
MAX_CASES = int(os.environ.get("PERSONA_FIELD_MAX_CASES", "0") or "0")

PERSONAS = [
    "cheer_sis",
    "soft_senior",
    "strict_trainer",
    "science_coach",
    "playful_buddy",
    "daily_manager",
]

FIELD_MARKERS: dict[str, dict[str, list[str]]] = {
    "cheer_sis": {
        "speech_level": ["요", "해요", "할게요", "드릴게요"],
        "relation_role": ["좋아요", "맞춰볼게요", "도와드릴게요", "밝게"],
        "emotional_tone": ["좋아요", "밝게", "충분해요"],
        "directive_style": ["가요", "볼게요", "작성할까요", "맞춰"],
        "evidence_style": ["부담", "충분", "시작", "근거", "안전"],
    },
    "soft_senior": {
        "speech_level": ["습니다", "됩니다", "해도 됩니다", "괜찮습니다"],
        "relation_role": ["괜찮습니다", "천천히", "적절합니다"],
        "emotional_tone": ["괜찮습니다", "무리 없게", "천천히"],
        "directive_style": ["해도 됩니다", "괜찮을까요", "제안드립니다"],
        "evidence_style": ["무리", "적절합니다", "한 단계", "근거"],
    },
    "strict_trainer": {
        "speech_level": ["해.", "해\n", "가.", "멈춰", "무리는 빼"],
        "relation_role": ["핵심", "바로", "무리는 빼", "멈춰"],
        "emotional_tone": ["바로", "핵심", "무리는 빼"],
        "directive_style": ["해", "가", "멈춰", "잡자", "분리해"],
        "evidence_style": ["안전 기준", "효율", "무리는 빼", "통증"],
    },
    "science_coach": {
        "speech_level": ["입니다", "습니다", "구성입니다"],
        "relation_role": ["기준은", "근거는", "선택 기준"],
        "emotional_tone": ["분석", "기준은", "근거는"],
        "directive_style": ["기준은", "구성입니다", "확인하겠습니다"],
        "evidence_style": ["근거는", "선택 기준", "안전성", "지속 가능성"],
    },
    "playful_buddy": {
        "speech_level": ["괜찮아", "가보자", "하자", "잡자"],
        "relation_role": ["같이", "가보자", "괜찮아"],
        "emotional_tone": ["오케이", "괜찮아", "가볍게"],
        "directive_style": ["가보자", "하자", "잡자"],
        "evidence_style": ["부담 낮게", "너무 크게", "괜찮아", "근거"],
    },
    "daily_manager": {
        "speech_level": ["확인했습니다", "항목입니다", "했습니다", "입니다"],
        "relation_role": ["확인했습니다", "반영 범위는", "처리합니다"],
        "emotional_tone": ["확인했습니다", "정리", "반영 범위는"],
        "directive_style": ["확인했습니다", "처리합니다", "정리했습니다"],
        "evidence_style": ["반영 범위는", "실행 기준", "캘린더 기준", "근거"],
    },
}

for persona_id, spec in PERSONA_STYLE_SPECS.items():
    FIELD_MARKERS[persona_id]["sentence_style"] = [str(marker) for marker in spec["sentence_style"]]

DISTINCTIVE_MARKERS = {
    "cheer_sis": ["가요", "맞춰볼게요", "밝게"],
    "soft_senior": ["괜찮습니다", "적절합니다", "해도 됩니다"],
    "strict_trainer": ["멈춰", "무리는 빼"],
    "science_coach": ["기준은", "근거는", "구성입니다"],
    "playful_buddy": ["괜찮아", "가보자"],
    "daily_manager": ["확인했습니다", "항목입니다", "반영 범위는"],
}


def build_base_profiles() -> list[dict[str, Any]]:
    return [
        {
            "base_profile_id": "p01",
            "age": 29,
            "gender": "female",
            "weight": 64,
            "exercise_level": "beginner",
            "activity_level": "low",
            "goal": "fat_loss",
            "lifestyle": "late commute office worker",
            "available_time_minutes": 18,
            "exercise_frequency": "주 2회",
            "injury_history": ["무릎"],
            "pain_points": ["무릎"],
            "medical_conditions": [],
            "allergies": ["우유"],
            "context_notes": ["late commute office worker", "fat_loss"],
        },
        {
            "base_profile_id": "p02",
            "age": 57,
            "gender": "male",
            "weight": 86,
            "exercise_level": "beginner",
            "activity_level": "low",
            "goal": "glucose_control",
            "lifestyle": "night driver",
            "available_time_minutes": 15,
            "exercise_frequency": "주 2회",
            "injury_history": [],
            "pain_points": [],
            "medical_conditions": ["type 2 diabetes"],
            "allergies": ["계란"],
            "context_notes": ["night driver", "glucose_control"],
        },
        {
            "base_profile_id": "p03",
            "age": 36,
            "gender": "male",
            "weight": 79,
            "exercise_level": "advanced",
            "activity_level": "moderate",
            "goal": "performance",
            "lifestyle": "CrossFit background",
            "available_time_minutes": 40,
            "exercise_frequency": "주 4회",
            "injury_history": ["손목"],
            "pain_points": ["손목"],
            "medical_conditions": [],
            "allergies": [],
            "context_notes": ["CrossFit background", "performance"],
        },
        {
            "base_profile_id": "p04",
            "age": 60,
            "gender": "male",
            "weight": 90,
            "exercise_level": "beginner",
            "activity_level": "low",
            "goal": "blood_pressure",
            "lifestyle": "dislikes gyms",
            "available_time_minutes": 20,
            "exercise_frequency": "주 2회",
            "injury_history": [],
            "pain_points": [],
            "medical_conditions": ["hypertension"],
            "allergies": [],
            "context_notes": ["dislikes gyms", "blood_pressure"],
        },
        {
            "base_profile_id": "p05",
            "age": 27,
            "gender": "female",
            "weight": 53,
            "exercise_level": "intermediate",
            "activity_level": "moderate",
            "goal": "muscle_gain",
            "lifestyle": "vegan designer",
            "available_time_minutes": 40,
            "exercise_frequency": "주 4회",
            "injury_history": [],
            "pain_points": [],
            "medical_conditions": [],
            "allergies": ["유당"],
            "diet_type": "vegan",
            "context_notes": ["vegan designer", "muscle_gain"],
        },
    ]


def build_persona_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for persona_id in PERSONAS:
        for base in build_base_profiles():
            profile = dict(base)
            profile["profile_id"] = f"{persona_id}-{base['base_profile_id']}"
            profile["selected_ai_persona"] = persona_id
            profiles.append(profile)
    return profiles


def _marker_hits(text: str, markers: list[str]) -> list[str]:
    normalized = str(text or "")
    return [marker for marker in markers if marker and marker in normalized]


def _applicable_persona_fields(turn: dict[str, Any]) -> list[str]:
    expected_action = turn.get("expected_action")
    expected_support = turn.get("expected_support")
    fields = ["speech_level", "relation_role", "emotional_tone", "sentence_style"]
    if expected_action in {"create", "modify", "info"}:
        fields.append("evidence_style")
    if expected_action in {"create", "modify"}:
        fields.append("directive_style")
    if expected_support == "care":
        fields.append("evidence_style")
    return fields


def _field_scores(text: str, persona_id: str, fields: list[str]) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for field in fields:
        markers = FIELD_MARKERS[persona_id][field]
        field_hits = _marker_hits(text, markers)
        hits[field] = field_hits
        scores[field] = 1.0 if field_hits else 0.0
    return scores, hits


def _wrong_persona_hits(text: str, persona_id: str) -> dict[str, list[str]]:
    wrong_hits: dict[str, list[str]] = {}
    for other_id, spec in PERSONA_STYLE_SPECS.items():
        if other_id == persona_id:
            continue
        hits = _marker_hits(text, DISTINCTIVE_MARKERS[other_id])
        if hits:
            wrong_hits[other_id] = hits
    return wrong_hits


def _sentence_anchor_coverage(text: str, persona_id: str) -> float:
    anchors = [str(marker) for marker in PERSONA_STYLE_SPECS[persona_id]["sentence_style"] if len(str(marker)) >= 2]
    if not anchors:
        return 1.0
    return round(len(_marker_hits(text, anchors)) / len(anchors), 3)


def evaluate_case(profile: dict[str, Any], turn: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    debug = response.get("debug_state") or {}
    response_text = str(response.get("response") or "")
    combined = _flatten_text([response_text, debug])
    persona_id = profile["selected_ai_persona"]
    expected_action = turn.get("expected_action")
    expected_domain = turn.get("expected_domain")
    expected_support = turn.get("expected_support")
    issues: list[str] = []
    scores: dict[str, float] = {}
    field_hits: dict[str, list[str]] = {}
    wrong_hits: dict[str, list[str]] = {}

    if expected_action:
        actual_action = debug.get("action_intent")
        scores["routing"] = 1.0 if actual_action == expected_action else 0.0
        if scores["routing"] < 1.0:
            issues.append(f"action_intent expected {expected_action}, got {actual_action}")
    else:
        scores["routing"] = 1.0

    if expected_domain:
        actual_domain = debug.get("domain")
        scores["domain"] = 1.0 if actual_domain == expected_domain else 0.0
        if scores["domain"] < 1.0:
            issues.append(f"domain expected {expected_domain}, got {actual_domain}")
    else:
        scores["domain"] = 1.0

    if expected_support:
        actual_support = debug.get("support_mode")
        scores["support_mode"] = 1.0 if actual_support == expected_support else 0.0
        if scores["support_mode"] < 1.0:
            issues.append(f"support_mode expected {expected_support}, got {actual_support}")

    resolved_persona = debug.get("resolved_persona_id")
    persona_applicable = expected_action != "safety"
    if persona_applicable:
        scores["persona_resolution"] = 1.0 if resolved_persona == persona_id else 0.0
        if scores["persona_resolution"] < 1.0:
            issues.append(f"persona expected {persona_id}, got {resolved_persona}")

        persona_field_scores, field_hits = _field_scores(response_text, persona_id, _applicable_persona_fields(turn))
        scores.update(persona_field_scores)
        for field, field_score in persona_field_scores.items():
            if field_score < 1.0:
                issues.append(f"{field} marker missing")
        wrong_hits = _wrong_persona_hits(response_text, persona_id)
        scores["persona_distinctiveness"] = 1.0 if not wrong_hits else 0.0
        if wrong_hits:
            issues.append(f"other persona markers leaked: {wrong_hits}")
        scores["sentence_anchor_coverage"] = _sentence_anchor_coverage(response_text, persona_id)
    else:
        safety_hit = _contains_any(response_text, SAFETY_MARKERS)
        scores["safety_priority"] = 1.0 if safety_hit else 0.0
        if not safety_hit:
            issues.append("safety answer lacks concrete safety markers")

    profile_markers = _profile_markers(profile)
    if expected_action in {"create", "modify", "info", "safety"} and profile_markers:
        scores["profile_accuracy"] = 1.0 if _contains_any(combined, profile_markers) else 0.0
        if scores["profile_accuracy"] < 1.0:
            issues.append(f"profile markers not reflected: {profile_markers[:5]}")
    else:
        scores["profile_accuracy"] = 1.0

    overall = round(statistics.mean(scores.values()), 3)
    grade = "pass" if overall >= 0.9 and not issues else "review" if overall >= 0.7 else "fail"
    return {
        "case_id": f"{profile['profile_id']}-{turn['turn_id']}",
        "profile_id": profile["profile_id"],
        "base_profile_id": profile["base_profile_id"],
        "turn_id": turn["turn_id"],
        "persona": persona_id,
        "persona_applicable": persona_applicable,
        "message": turn["message"],
        "grade": grade,
        "overall": overall,
        "scores": scores,
        "field_hits": field_hits,
        "wrong_persona_hits": wrong_hits,
        "issues": issues,
        "response_excerpt": response_text[:360],
        "debug": {
            "action_intent": debug.get("action_intent"),
            "domain": debug.get("domain"),
            "support_mode": debug.get("support_mode"),
            "resolved_persona_id": resolved_persona,
        },
    }


def evaluate_timeout_case(profile: dict[str, Any], turn: dict[str, Any], reason: str) -> dict[str, Any]:
    persona_id = profile["selected_ai_persona"]
    return {
        "case_id": f"{profile['profile_id']}-{turn['turn_id']}",
        "profile_id": profile["profile_id"],
        "base_profile_id": profile["base_profile_id"],
        "turn_id": turn["turn_id"],
        "persona": persona_id,
        "persona_applicable": turn.get("expected_action") != "safety",
        "message": turn["message"],
        "grade": "fail",
        "overall": 0.0,
        "scores": {"request_completed": 0.0},
        "field_hits": {},
        "wrong_persona_hits": {},
        "issues": [reason],
        "response_excerpt": "",
        "debug": {},
    }


def _average(values: list[float]) -> float:
    return round(statistics.mean(values), 3) if values else 1.0


def build_report(evaluations: list[dict[str, Any]], profiles: list[dict[str, Any]], turns: list[dict[str, Any]]) -> dict[str, Any]:
    field_names = [
        "speech_level",
        "relation_role",
        "emotional_tone",
        "directive_style",
        "evidence_style",
        "sentence_style",
    ]
    applicable = [item for item in evaluations if item["persona_applicable"]]
    summary = {
        "persona_count": len(PERSONAS),
        "base_profile_count": 5,
        "persona_profile_count": len(profiles),
        "turn_count": len(turns),
        "case_count": len(evaluations),
        "persona_applicable_case_count": len(applicable),
        "safety_priority_case_count": len(evaluations) - len(applicable),
        "pass_count": sum(1 for item in evaluations if item["grade"] == "pass"),
        "review_count": sum(1 for item in evaluations if item["grade"] == "review"),
        "fail_count": sum(1 for item in evaluations if item["grade"] == "fail"),
        "overall_average": _average([item["overall"] for item in evaluations]),
        "field_average": {
            field: _average([item["scores"][field] for item in applicable if field in item["scores"]])
            for field in field_names
        },
        "persona_distinctiveness_average": _average(
            [item["scores"]["persona_distinctiveness"] for item in applicable if "persona_distinctiveness" in item["scores"]]
        ),
        "sentence_anchor_coverage_average": _average(
            [item["scores"]["sentence_anchor_coverage"] for item in applicable if "sentence_anchor_coverage" in item["scores"]]
        ),
        "routing_average": _average([item["scores"]["routing"] for item in evaluations]),
        "profile_accuracy_average": _average([item["scores"]["profile_accuracy"] for item in evaluations]),
        "safety_priority_average": _average(
            [item["scores"]["safety_priority"] for item in evaluations if "safety_priority" in item["scores"]]
        ),
    }
    persona_field_average = {}
    for persona_id in PERSONAS:
        persona_items = [item for item in applicable if item["persona"] == persona_id]
        persona_field_average[persona_id] = {
            field: _average([item["scores"][field] for item in persona_items if field in item["scores"]])
            for field in field_names
        }
        persona_field_average[persona_id]["persona_distinctiveness"] = _average(
            [item["scores"]["persona_distinctiveness"] for item in persona_items if "persona_distinctiveness" in item["scores"]]
        )
        persona_field_average[persona_id]["sentence_anchor_coverage"] = _average(
            [item["scores"]["sentence_anchor_coverage"] for item in persona_items if "sentence_anchor_coverage" in item["scores"]]
        )

    return {
        "summary": summary,
        "persona_field_average": persona_field_average,
        "persona_contract": PERSONA_STYLE_SPECS,
        "field_markers": FIELD_MARKERS,
        "dataset": {"profiles": profiles, "turns": turns},
        "evaluations": evaluations,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Persona Field 6x5x20 Report",
        "",
        "## Summary",
        f"- Total cases: {summary['case_count']}",
        f"- Personas/Base profiles/Turns: {summary['persona_count']}/{summary['base_profile_count']}/{summary['turn_count']}",
        f"- Persona-applicable cases: {summary['persona_applicable_case_count']}",
        f"- Safety-priority cases: {summary['safety_priority_case_count']}",
        f"- Pass/Review/Fail: {summary['pass_count']}/{summary['review_count']}/{summary['fail_count']}",
        f"- Overall average: {summary['overall_average']}",
        f"- Persona distinctiveness average: {summary['persona_distinctiveness_average']}",
        f"- Sentence anchor coverage average: {summary['sentence_anchor_coverage_average']}",
        f"- Routing/Profile/Safety: {summary['routing_average']}/{summary['profile_accuracy_average']}/{summary['safety_priority_average']}",
        "",
        "## Field Average",
    ]
    for field, score in summary["field_average"].items():
        lines.append(f"- {field}: {score}")
    lines.extend(["", "## Persona Field Average", "| Persona | Speech | Role | Tone | Directive | Evidence | Sentence | Distinct | Anchor |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for persona_id, scores in report["persona_field_average"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    persona_id,
                    str(scores["speech_level"]),
                    str(scores["relation_role"]),
                    str(scores["emotional_tone"]),
                    str(scores["directive_style"]),
                    str(scores["evidence_style"]),
                    str(scores["sentence_style"]),
                    str(scores["persona_distinctiveness"]),
                    str(scores["sentence_anchor_coverage"]),
                ]
            )
            + " |"
        )
    non_pass = [item for item in report["evaluations"] if item["grade"] != "pass"]
    lines.extend(["", "## Non-Pass Cases"])
    if not non_pass:
        lines.append("- None")
    else:
        for item in non_pass[:50]:
            lines.append(f"- {item['case_id']} {item['grade']} overall={item['overall']}")
            lines.append(f"  - issues: {item['issues']}")
    return "\n".join(lines) + "\n"


async def run_suite() -> dict[str, Any]:
    profiles = build_persona_profiles()
    turns = build_turns()
    print(
        f"[persona-field-6x5x20] start profiles={len(profiles)} turns={len(turns)} max_cases={MAX_CASES or 'all'}",
        flush=True,
    )
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        (CHECKPOINT_DB_PATH.parent / f"{CHECKPOINT_DB_PATH.name}{suffix}").unlink(missing_ok=True)
    DATA_PATH.write_text(json.dumps({"profiles": profiles, "turns": turns}, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[persona-field-6x5x20] building test stack", flush=True)
    app, _graph, _deps, _fake_was, checkpointer = await build_test_stack(fake_router=PersonaContractRouter())
    print("[persona-field-6x5x20] test stack ready", flush=True)
    transport = httpx.ASGITransport(app=app)
    evaluations: list[dict[str, Any]] = []
    completed = 0
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for profile in profiles:
            session_id: str | None = None
            user_id = f"persona-field-{profile['profile_id']}-{uuid.uuid4().hex[:6]}"
            for turn in turns:
                try:
                    response = await asyncio.wait_for(
                        run_request(
                            client,
                            user_id=user_id,
                            message=turn["message"],
                            profile=profile,
                            session_id=session_id,
                        ),
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    session_id = response.get("session_id") or session_id
                    evaluations.append(evaluate_case(profile, turn, response))
                except TimeoutError:
                    evaluations.append(
                        evaluate_timeout_case(
                            profile,
                            turn,
                            f"request timed out after {REQUEST_TIMEOUT_SECONDS}s",
                        )
                    )
                completed += 1
                if completed % 20 == 0:
                    print(
                        f"[persona-field-6x5x20] progress {completed}/{len(profiles) * len(turns)} "
                        f"last={profile['profile_id']}-{turn['turn_id']}",
                        flush=True,
                    )
                if MAX_CASES and completed >= MAX_CASES:
                    break
            if MAX_CASES and completed >= MAX_CASES:
                break

    if checkpointer is not None:
        close = getattr(checkpointer, "close", None)
        if callable(close):
            close_result = close()
            if inspect.isawaitable(close_result):
                await close_result
        conn = getattr(checkpointer, "conn", None) or getattr(checkpointer, "_conn", None)
        conn_close = getattr(conn, "close", None)
        if callable(conn_close):
            with contextlib.suppress(Exception):
                conn_close_result = conn_close()
                if inspect.isawaitable(conn_close_result):
                    await conn_close_result
    temp_dir = getattr(getattr(app, "state", None), "_temp_dir", None)
    cleanup = getattr(temp_dir, "cleanup", None)
    if callable(cleanup):
        with contextlib.suppress(Exception):
            cleanup()

    report = build_report(evaluations, profiles, turns)
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD_PATH.write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    report = asyncio.run(run_suite())
    summary = report["summary"]
    print(f"[persona-field-6x5x20] summary: {json.dumps(summary, ensure_ascii=False)}")
    print(f"[persona-field-6x5x20] report json: {REPORT_JSON_PATH}")
    print(f"[persona-field-6x5x20] report md: {REPORT_MD_PATH}")
    if summary["fail_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
