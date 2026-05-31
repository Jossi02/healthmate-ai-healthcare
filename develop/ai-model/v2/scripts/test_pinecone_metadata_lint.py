"""Static lint for the curated Pinecone external knowledge catalog."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "external_knowledge_v2.json"
REPORT_JSON_PATH = ROOT / "docs" / "quality" / "pinecone_metadata_lint_report.json"
REPORT_MD_PATH = ROOT / "docs" / "quality" / "pinecone_metadata_lint_report.md"

REQUIRED_FIELDS = {
    "kb_id": str,
    "source_type": str,
    "source": str,
    "source_title": str,
    "url": str,
    "year": int,
    "domain": str,
    "topic": str,
    "category": str,
    "use_cases": list,
    "population": str,
    "profile_targets": list,
    "constraints": list,
    "goals": list,
    "risk_level": str,
    "evidence_type": str,
    "evidence_rank": int,
    "chunk_title": str,
    "tags": list,
    "text": str,
}

ALLOWED_DOMAINS = {"workout", "diet"}
ALLOWED_RISK_LEVELS = {"low", "caution", "avoid"}
ALLOWED_PROFILE_TARGETS = {
    "general_adult",
    "beginner",
    "advanced",
    "older_adult",
    "minor",
    "high_weight",
    "food_allergy",
    "plant_based",
}
ALLOWED_CONSTRAINTS = {
    "arthritis",
    "asthma",
    "back_pain",
    "balance_risk",
    "breathlessness",
    "cardiovascular_disease",
    "chest_pain",
    "dairy_allergy",
    "diabetes",
    "dizziness",
    "egg_allergy",
    "extreme_diet_risk",
    "food_allergy",
    "hypertension",
    "kidney_caution",
    "knee_pain",
    "low_time",
    "mobility_limitation",
    "nut_allergy",
    "obesity",
    "shellfish_allergy",
    "shoulder_pain",
    "soy_allergy",
    "vegan",
    "vegetarian",
    "wheat_allergy",
}
ALLOWED_GOALS = {
    "bone_health",
    "fat_loss",
    "glucose_control",
    "habit",
    "heart_health",
    "mobility",
    "muscle_gain",
}
ALLOWED_USE_CASES = {
    "allergy_safe_planning",
    "cardio_programming",
    "coaching",
    "evidence_interpretation",
    "fat_loss",
    "glucose_control",
    "heart_health",
    "hiit_programming",
    "hypertrophy_programming",
    "info_answer",
    "injury_prevention",
    "meal_planning",
    "mobility",
    "muscle_gain",
    "novice_programming",
    "older_adults",
    "plan_create",
    "plan_modify",
    "program_design",
    "program_adjustment",
    "risk_repair",
    "risk_screening",
    "strength_programming",
    "supplement_use",
    "technique_cueing",
    "training_day_nutrition",
    "warmup",
}
WORKOUT_TOPICS = {
    "cardio",
    "hiit",
    "mobility",
    "pain_adaptation",
    "physical_activity",
    "resistance_training",
}
DIET_TOPICS = {
    "diabetes_nutrition",
    "food_allergy",
    "heart_health_nutrition",
    "meal_planning",
    "meal_timing",
    "protein",
    "supplement",
}


def main() -> None:
    items = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(items, list):
        raise SystemExit("external knowledge JSON must be a list")

    ids = [str(item.get("kb_id") or "") for item in items if isinstance(item, dict)]
    for kb_id in sorted({kb_id for kb_id in ids if ids.count(kb_id) > 1}):
        issues.append(f"duplicate kb_id: {kb_id}")

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            issues.append(f"row {index}: item must be an object")
            continue
        kb_id = str(item.get("kb_id") or f"row_{index}")

        for field, expected_type in REQUIRED_FIELDS.items():
            if field not in item:
                issues.append(f"{kb_id}: missing required field {field}")
                continue
            if not isinstance(item[field], expected_type):
                issues.append(f"{kb_id}: field {field} must be {expected_type.__name__}")

        if item.get("source_type") != "external_kb":
            issues.append(f"{kb_id}: source_type must be external_kb")
        if item.get("source") != "external":
            issues.append(f"{kb_id}: source must be external")
        if item.get("domain") not in ALLOWED_DOMAINS:
            issues.append(f"{kb_id}: invalid domain {item.get('domain')}")
        if item.get("risk_level") not in ALLOWED_RISK_LEVELS:
            issues.append(f"{kb_id}: invalid risk_level {item.get('risk_level')}")

        topic = item.get("topic")
        if item.get("domain") == "workout" and topic not in WORKOUT_TOPICS:
            issues.append(f"{kb_id}: workout row has non-workout topic {topic}")
        if item.get("domain") == "diet" and topic not in DIET_TOPICS:
            issues.append(f"{kb_id}: diet row has non-diet topic {topic}")

        _check_allowed_list(issues, kb_id, "profile_targets", item.get("profile_targets"), ALLOWED_PROFILE_TARGETS)
        _check_allowed_list(issues, kb_id, "constraints", item.get("constraints"), ALLOWED_CONSTRAINTS)
        _check_allowed_list(issues, kb_id, "goals", item.get("goals"), ALLOWED_GOALS)
        _check_allowed_list(issues, kb_id, "use_cases", item.get("use_cases"), ALLOWED_USE_CASES)

        constraints = item.get("constraints") or []
        if len(constraints) >= 6 and "food_allergy" not in constraints:
            warnings.append(f"{kb_id}: broad constraint row without food_allergy")
        if item.get("domain") == "diet" and any(goal in item.get("goals", []) for goal in ("mobility",)):
            issues.append(f"{kb_id}: diet row has workout-only goal")
        if item.get("domain") == "workout" and any(goal in item.get("goals", []) for goal in ("glucose_control",)):
            warnings.append(f"{kb_id}: workout row uses diet-adjacent goal glucose_control")

        text = str(item.get("text") or "")
        if len(text) < 60:
            issues.append(f"{kb_id}: text too short")
        if not str(item.get("url") or "").startswith("http"):
            issues.append(f"{kb_id}: url must be http(s)")
        rank = item.get("evidence_rank")
        if isinstance(rank, int) and not 1 <= rank <= 5:
            issues.append(f"{kb_id}: evidence_rank must be 1..5")
        year = item.get("year")
        if isinstance(year, int) and not 2000 <= year <= 2027:
            issues.append(f"{kb_id}: suspicious year {year}")

    report = {
        "summary": {
            "rows": len(items),
            "issues": len(issues),
            "warnings": len(warnings),
            "passed": not issues,
        },
        "issues": issues,
        "warnings": warnings,
    }
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD_PATH.write_text(_render_markdown(report), encoding="utf-8")

    print("[pinecone-metadata-lint] summary:", json.dumps(report["summary"], ensure_ascii=False))
    print("[pinecone-metadata-lint] report json:", REPORT_JSON_PATH)
    print("[pinecone-metadata-lint] report md:", REPORT_MD_PATH)
    if issues:
        raise SystemExit(1)


def _check_allowed_list(
    issues: list[str],
    kb_id: str,
    field: str,
    values: Any,
    allowed: set[str],
) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        if value not in allowed:
            issues.append(f"{kb_id}: unknown {field} tag {value}")


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Pinecone Metadata Lint Report",
        "",
        f"- Rows: {summary['rows']}",
        f"- Passed: {summary['passed']}",
        f"- Issues: {summary['issues']}",
        f"- Warnings: {summary['warnings']}",
        "",
        "## Issues",
    ]
    lines.extend(f"- {issue}" for issue in report["issues"] or ["none"])
    lines.append("")
    lines.append("## Warnings")
    lines.extend(f"- {warning}" for warning in report["warnings"] or ["none"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
