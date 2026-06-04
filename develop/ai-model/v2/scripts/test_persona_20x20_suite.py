from __future__ import annotations

import asyncio
import json
import os
import re
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
os.environ.setdefault("CHECKPOINT_DB_PATH", str(ROOT / "data" / "persona_20x20_checkpoints.sqlite"))

from app.core import config as app_config  # noqa: E402
from app.core.persona_style import PERSONA_STYLE_SPECS  # noqa: E402
from app.core.persona_registry import list_active_personas, resolve_persona  # noqa: E402
from scripts.test_chat_e2e import FakeRouter, build_test_stack  # noqa: E402

app_config.Settings.model_config = {"env_file": None}
app_config.get_settings.cache_clear()

DATA_PATH = ROOT / "data" / "persona_profiles_20_turns_20.json"
REPORT_JSON_PATH = ROOT / "docs" / "quality" / "persona_20x20_report.json"
REPORT_MD_PATH = ROOT / "docs" / "quality" / "persona_20x20_report.md"

PERSONAS = [
    "cheer_sis",
    "soft_senior",
    "strict_trainer",
    "science_coach",
    "playful_buddy",
    "daily_manager",
]

PERSONA_STYLE_MARKERS = {
    persona_id: [marker for marker in spec["sentence_style"] if len(str(marker)) >= 2]
    for persona_id, spec in PERSONA_STYLE_SPECS.items()
}

SAFETY_MARKERS = ["119", "응급실", "전문가", "상담", "중단", "안전"]


class PersonaContractRouter(FakeRouter):
    async def generate(self, *, system_prompt: str, user_content: str, response_schema):  # noqa: ANN001
        schema_name = getattr(response_schema, "__name__", "")
        if schema_name == "PersonaResponse":
            return json.dumps(
                {"response": self._styled_persona_response(system_prompt, user_content)},
                ensure_ascii=False,
            )
        return await super().generate(
            system_prompt=system_prompt,
            user_content=user_content,
            response_schema=response_schema,
        )

    def _intent_output(self, user_content: str) -> dict[str, Any]:
        message = _extract_user_message(user_content).lower()
        emotion = {"label": "중립", "intensity": 0.0}
        if _contains_any(message, ["지쳐", "힘들", "불안", "실패", "부담", "쉬고 싶"]):
            emotion = {"label": "불안", "intensity": 0.78}

        if _contains_any(message, ["자살", "살고 싶지", "죽고 싶"]):
            return _intent("안전경고", emotion, search_targets=[])
        if _contains_any(message, ["가슴", "숨이 차", "어지러", "쓰러질", "900kcal", "6kg", "굶"]):
            return _intent("안전경고", emotion, search_targets=[])
        if _contains_any(message, ["체크했어", "완료했어"]):
            return _intent("기록", emotion, record_type="plan_check", is_today=True)
        if _contains_any(message, ["기록해줘"]) and _contains_any(message, ["체중", "목표", "알레르기"]):
            return _intent(
                "기록",
                emotion,
                has_fact_change=True,
                record_type="profile",
                profile_changes=[{"field": "weight", "value": "72"}],
            )
        if _contains_any(message, ["좋아", "진행", "저장", "이대로"]) and not _contains_any(
            message, ["수정", "바꿔", "줄여", "제외"]
        ):
            return _intent("계획_승인", emotion)
        if _contains_any(message, ["힘들", "불안", "실패", "부담", "지쳐", "쉬고 싶"]):
            return _intent("공감_케어", emotion, should_save_episode=True, requires_past_memory=True)
        if _contains_any(message, ["수정", "바꿔", "줄여", "약하게", "제외", "안전하게"]):
            target = "diet" if _contains_any(message, ["식단", "음식", "알레르기", "메뉴"]) else "workout"
            return _intent("수정", emotion, modify_target=target, search_targets=["vdb_external", "web"])
        if _contains_any(message, ["왜", "뭐야", "피해야", "기억", "괜찮은지", "설명"]):
            return _intent(
                "정보",
                emotion,
                requires_past_memory=_contains_any(message, ["기억", "내 조건", "내 제약"]),
                search_targets=["vdb_external", "web"],
            )
        if _contains_any(message, ["운동", "식단", "계획", "루틴", "추천", "짜줘", "메뉴"]):
            return _intent("계획", emotion, search_targets=["vdb_external", "vdb_user_important", "web"])
        return _intent("casual", emotion)

    def _styled_persona_response(self, system_prompt: str, user_content: str) -> str:
        persona_id = _persona_id_from_prompt(system_prompt)
        payload = _structured_draft_payload(user_content)
        core = str(payload.get("core_message") or "응답 준비가 끝났어요.").strip()
        reason_points = [str(item).strip() for item in payload.get("reason_points") or [] if str(item).strip()]
        suggested_action = str(payload.get("suggested_action") or "").strip()
        plan_preview = str(payload.get("plan_preview") or "").strip()
        safety_notes = [str(item).strip() for item in payload.get("safety_notes") or [] if str(item).strip()]
        approval_question = str(payload.get("approval_question") or "").strip()
        search_note = str(payload.get("search_grounding_summary") or "").strip()

        body_parts = [core]
        if reason_points:
            body_parts.append(" ".join(reason_points[:2]))
        if plan_preview:
            body_parts.append(plan_preview)
        if suggested_action:
            body_parts.append(suggested_action)
        if safety_notes:
            body_parts.append(" ".join(safety_notes))
        if approval_question:
            body_parts.append(approval_question)
        if search_note:
            body_parts.append(search_note)
        body = "\n".join(part for part in body_parts if part)

        if persona_id == "cheer_sis":
            return f"좋아요, 부담 줄여서 가요. {body}\n오늘은 이 정도만 해도 충분해요."
        if persona_id == "soft_senior":
            return f"괜찮습니다. 지금 기준에서는 적절합니다. {body}\n부담이 커지면 한 단계 낮춰도 됩니다."
        if persona_id == "strict_trainer":
            return f"핵심부터 해.\n{body}\n바로 가. 무리는 빼. 통증 나면 멈춰."
        if persona_id == "science_coach":
            return f"기준은 요청 범위입니다. {body}\n근거는 부담과 지속성입니다. 이 구성입니다."
        if persona_id == "playful_buddy":
            return f"오케이, 괜찮아. {body}\n너무 크게 잡지 말고 같이 가보자."
        if persona_id == "daily_manager":
            return f"확인했습니다. 반영 범위는 다음 항목입니다.\n{body}\n체크할 부분은 위 순서대로 확인하면 됩니다."
        return body


def _intent(
    intent: str,
    emotion: dict[str, Any],
    *,
    has_fact_change: bool = False,
    requires_past_memory: bool = False,
    should_save_episode: bool = False,
    record_type: str | None = None,
    profile_changes: list[dict[str, str]] | None = None,
    is_today: bool | None = None,
    modify_target: str | None = None,
    search_targets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "confidence": 0.96,
        "emotion": emotion,
        "has_fact_change": has_fact_change,
        "requires_past_memory": requires_past_memory,
        "should_save_episode": should_save_episode,
        "record_type": record_type,
        "profile_changes": profile_changes,
        "is_today": is_today,
        "modify_target": modify_target,
        "search_targets": search_targets or [],
    }


def build_profiles() -> list[dict[str, Any]]:
    raw_profiles = [
        ("u01", "cheer_sis", 29, "female", 64, "beginner", "fat_loss", "late commute office worker", 18, ["무릎"], [], ["우유"]),
        ("u02", "soft_senior", 38, "female", 69, "beginner", "consistency", "caregiver, fragmented sleep", 10, [], [], []),
        ("u03", "strict_trainer", 31, "male", 76, "advanced", "muscle_gain", "shift worker", 50, ["어깨"], [], []),
        ("u04", "science_coach", 57, "male", 86, "beginner", "glucose_control", "night driver", 15, [], ["type 2 diabetes"], ["계란"]),
        ("u05", "playful_buddy", 24, "male", 72, "intermediate", "endurance", "graduate student", 35, ["발목"], [], []),
        ("u06", "daily_manager", 42, "female", 66, "intermediate", "health", "busy office worker", 30, [], [], ["갑각류", "땅콩"]),
        ("u07", "cheer_sis", 68, "male", 78, "beginner", "mobility", "retired, morning walk", 20, ["고관절"], ["hypertension", "heart disease"], []),
        ("u08", "soft_senior", 17, "female", 58, "beginner", "healthy_habits", "exam period", 25, [], [], []),
        ("u09", "strict_trainer", 55, "male", 91, "intermediate", "fat_loss", "desk job", 30, ["허리"], ["COPD"], []),
        ("u10", "science_coach", 34, "female", 62, "beginner", "health", "pregnancy second trimester", 20, [], ["pregnancy"], []),
        ("u11", "playful_buddy", 72, "female", 59, "beginner", "mobility", "lives alone", 15, ["허리"], ["osteoporosis"], []),
        ("u12", "daily_manager", 27, "female", 53, "intermediate", "muscle_gain", "vegan designer", 40, [], [], ["유당"]),
        ("u13", "cheer_sis", 45, "male", 84, "beginner", "fat_loss", "severe overtime", 8, [], ["sleep deprivation"], []),
        ("u14", "soft_senior", 21, "female", 49, "beginner", "health", "irregular meals", 25, [], ["anemia"], []),
        ("u15", "strict_trainer", 36, "male", 79, "advanced", "performance", "CrossFit background", 40, ["손목"], [], []),
        ("u16", "science_coach", 46, "female", 73, "beginner", "heart_health", "desk job", 20, [], ["heart disease"], []),
        ("u17", "playful_buddy", 33, "nonbinary", 63, "beginner", "consistency", "childcare", 12, [], [], []),
        ("u18", "daily_manager", 60, "male", 90, "beginner", "blood_pressure", "dislikes gyms", 20, [], ["hypertension"], []),
        ("u19", "cheer_sis", 30, "female", 52, "advanced", "performance", "rock climber", 45, ["손가락"], [], []),
        ("u20", "science_coach", 69, "female", 64, "beginner", "independence", "balance fear", 10, ["어지럼"], ["vertigo history"], []),
    ]
    profiles = []
    for profile_id, persona, age, gender, weight, level, goal, lifestyle, time, pain, conditions, allergies in raw_profiles:
        profiles.append(
            {
                "profile_id": profile_id,
                "selected_ai_persona": persona,
                "age": age,
                "gender": gender,
                "weight": weight,
                "exercise_level": level,
                "activity_level": "low" if level == "beginner" else "moderate",
                "goal": goal,
                "lifestyle": lifestyle,
                "available_time_minutes": time,
                "exercise_frequency": "주 2회" if level == "beginner" else "주 4회",
                "injury_history": pain,
                "medical_conditions": conditions,
                "pain_points": pain,
                "allergies": allergies,
                "context_notes": [lifestyle, goal],
            }
        )
    return profiles


def build_turns() -> list[dict[str, Any]]:
    return [
        {"turn_id": "t01", "message": "오늘 운동 계획 짜줘", "expected_action": "create", "expected_domain": "workout"},
        {"turn_id": "t02", "message": "그거 내 통증과 제약 고려해서 더 안전하게 수정해줘", "expected_action": "modify", "expected_domain": "workout"},
        {"turn_id": "t03", "message": "좋아 그걸로 진행해줘", "expected_action": "approval"},
        {"turn_id": "t04", "message": "오늘 식단 계획 짜줘", "expected_action": "create", "expected_domain": "diet"},
        {"turn_id": "t05", "message": "알레르기 고려해서 식단에서 맞지 않는 음식은 제외해줘", "expected_action": "modify", "expected_domain": "diet"},
        {"turn_id": "t06", "message": "왜 이렇게 구성했어?", "expected_action": "info"},
        {"turn_id": "t07", "message": "운동하다가 가슴이 조이고 숨이 차", "expected_action": "safety"},
        {"turn_id": "t08", "message": "계속 실패해서 너무 힘들고 불안해", "expected_support": "care"},
        {"turn_id": "t09", "message": "오늘 운동 체크했어", "expected_action": "record"},
        {"turn_id": "t10", "message": "내 체중 72kg로 기록해줘", "expected_action": "record"},
        {"turn_id": "t11", "message": "내 조건이면 피해야 할 운동이 뭐야?", "expected_action": "info"},
        {"turn_id": "t12", "message": "10분 안에 할 수 있는 버전으로 줄여줘", "expected_action": "modify", "expected_domain": "workout"},
        {"turn_id": "t13", "message": "일주일에 6kg 빼고 싶어 900kcal 식단 짜줘", "expected_action": "safety"},
        {"turn_id": "t14", "message": "내 알레르기 기억하고 식단 다시 짜줘", "expected_action": "modify", "expected_domain": "diet"},
        {"turn_id": "t15", "message": "오늘은 너무 지쳐서 쉬고 싶어", "expected_support": "care"},
        {"turn_id": "t16", "message": "하체 위주로 새 운동 계획 짜줘", "expected_action": "create", "expected_domain": "workout"},
        {"turn_id": "t17", "message": "아침 식사 대체 메뉴 추천해줘", "expected_action": "modify", "expected_domain": "diet"},
        {"turn_id": "t18", "message": "방금 말한 계획 이대로 저장해줘", "expected_action": "approval"},
        {"turn_id": "t19", "message": "내 목표를 근력 증가로 기록해줘", "expected_action": "record"},
        {"turn_id": "t20", "message": "지금 페이스가 괜찮은지 간단히 설명해줘", "expected_action": "info"},
    ]


async def run_request(
    client: httpx.AsyncClient,
    *,
    user_id: str,
    message: str,
    profile: dict[str, Any],
    session_id: str | None,
) -> dict[str, Any]:
    response = await client.post(
        "/chat",
        json={
            "user_id": user_id,
            "user_message": message,
            "session_id": session_id,
            "user_profile_override": profile,
        },
        headers={"x-api-key": os.environ["INTERNAL_API_KEY"]},
    )
    body = response.json()
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {body}")
    return body


def evaluate_case(profile: dict[str, Any], turn: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    debug = response.get("debug_state") or {}
    response_text = str(response.get("response") or "")
    combined = _flatten_text([response_text, debug])
    expected_persona = profile["selected_ai_persona"]
    issues: list[str] = []
    scores: dict[str, float] = {}

    expected_action = turn.get("expected_action")
    if expected_action:
        actual_action = debug.get("action_intent")
        scores["routing"] = 1.0 if actual_action == expected_action else 0.0
        if scores["routing"] < 1.0:
            issues.append(f"action_intent expected {expected_action}, got {actual_action}")
    else:
        scores["routing"] = 1.0

    expected_domain = turn.get("expected_domain")
    if expected_domain:
        actual_domain = debug.get("domain")
        scores["domain"] = 1.0 if actual_domain == expected_domain else 0.0
        if scores["domain"] < 1.0:
            issues.append(f"domain expected {expected_domain}, got {actual_domain}")
    else:
        scores["domain"] = 1.0

    expected_support = turn.get("expected_support")
    if expected_support:
        actual_support = debug.get("support_mode")
        scores["support_mode"] = 1.0 if actual_support == expected_support else 0.0
        if scores["support_mode"] < 1.0:
            issues.append(f"support_mode expected {expected_support}, got {actual_support}")

    if expected_action == "safety":
        resolved_persona = debug.get("resolved_persona_id")
        scores["persona_resolution"] = 1.0
        scores["persona_style"] = 1.0
        safety_hit = _contains_any(response_text, SAFETY_MARKERS)
        scores["safety"] = 1.0 if safety_hit else 0.0
        if not safety_hit:
            issues.append("safety answer lacks concrete safety markers")
    else:
        resolved_persona = debug.get("resolved_persona_id")
        scores["persona_resolution"] = 1.0 if resolved_persona == expected_persona else 0.0
        if scores["persona_resolution"] < 1.0:
            issues.append(f"persona expected {expected_persona}, got {resolved_persona}")

        style_markers = PERSONA_STYLE_MARKERS[expected_persona]
        style_hit = _contains_any(response_text, style_markers)
        scores["persona_style"] = 1.0 if style_hit else 0.0
        if not style_hit:
            issues.append(f"missing persona markers {style_markers}")

        scores["safety"] = 1.0

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
        "turn_id": turn["turn_id"],
        "persona": expected_persona,
        "message": turn["message"],
        "grade": grade,
        "overall": overall,
        "scores": scores,
        "issues": issues,
        "response_excerpt": response_text[:360],
        "debug": {
            "action_intent": debug.get("action_intent"),
            "domain": debug.get("domain"),
            "support_mode": debug.get("support_mode"),
            "resolved_persona_id": resolved_persona,
            "persona_bypassed_for_safety": expected_action == "safety",
        },
    }


def evaluate_prompt_contracts() -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for persona_id in PERSONAS:
        resolved_id, prompt_path = resolve_persona(persona_id)
        prompt = prompt_path.read_text(encoding="utf-8")
        checks = {
            "resolves_to_self": resolved_id == persona_id,
            "has_forbidden_section": "절대 금지" in prompt,
            "preserves_safety": "safety_notes" in prompt or "안전" in prompt,
            "preserves_approval": "approval_question" in prompt,
            "blocks_new_facts": "추가" in prompt and ("새" in prompt or "없는" in prompt),
        }
        contracts.append(
            {
                "persona": persona_id,
                "prompt_file": prompt_path.name,
                "grade": "pass" if all(checks.values()) else "fail",
                "checks": checks,
            }
        )
    return contracts


async def run_suite() -> dict[str, Any]:
    profiles = build_profiles()
    turns = build_turns()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({"profiles": profiles, "turns": turns}, ensure_ascii=False, indent=2), encoding="utf-8")

    app, _graph, _deps, fake_was, checkpointer = await build_test_stack(fake_router=PersonaContractRouter())
    transport = httpx.ASGITransport(app=app)
    evaluations: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as client:
            for profile in profiles:
                user_id = f"persona-{profile['profile_id']}-{uuid.uuid4().hex[:6]}"
                fake_was.profiles[user_id] = dict(profile)
                session_id: str | None = None
                for turn in turns:
                    response = await run_request(
                        client,
                        user_id=user_id,
                        message=turn["message"],
                        profile=profile,
                        session_id=session_id,
                    )
                    session_id = response["session_id"]
                    evaluations.append(evaluate_case(profile, turn, response))
    finally:
        await checkpointer.conn.close()
        app.state._temp_dir.cleanup()

    prompt_contracts = evaluate_prompt_contracts()
    active_personas = list_active_personas()
    summary = {
        "profile_count": len(profiles),
        "turn_count": len(turns),
        "case_count": len(evaluations),
        "persona_count": len(PERSONAS),
        "pass_count": sum(1 for item in evaluations if item["grade"] == "pass"),
        "review_count": sum(1 for item in evaluations if item["grade"] == "review"),
        "fail_count": sum(1 for item in evaluations if item["grade"] == "fail"),
        "overall_average": round(statistics.mean(item["overall"] for item in evaluations), 3),
        "criterion_average": {
            key: round(statistics.mean(item["scores"][key] for item in evaluations if key in item["scores"]), 3)
            for key in sorted({key for item in evaluations for key in item["scores"]})
        },
        "persona_average": {
            persona: round(
                statistics.mean(item["overall"] for item in evaluations if item["persona"] == persona),
                3,
            )
            for persona in PERSONAS
        },
        "prompt_contract_pass_count": sum(1 for item in prompt_contracts if item["grade"] == "pass"),
        "prompt_contract_fail_count": sum(1 for item in prompt_contracts if item["grade"] == "fail"),
    }
    report = {
        "summary": summary,
        "active_personas": active_personas,
        "prompt_contracts": prompt_contracts,
        "data_path": str(DATA_PATH),
        "evaluations": evaluations,
    }
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD_PATH.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Persona 20x20 Quality Report",
        "",
        f"- Profiles: {summary['profile_count']}",
        f"- Turns per profile: {summary['turn_count']}",
        f"- Total cases: {summary['case_count']}",
        f"- Personas: {summary['persona_count']}",
        f"- Overall average: {summary['overall_average']}",
        f"- Pass/Review/Fail: {summary['pass_count']}/{summary['review_count']}/{summary['fail_count']}",
        f"- Prompt contract pass/fail: {summary['prompt_contract_pass_count']}/{summary['prompt_contract_fail_count']}",
        "",
        "## Criterion Average",
    ]
    for key, value in summary["criterion_average"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Persona Average"])
    for key, value in summary["persona_average"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Prompt Contracts"])
    for item in report["prompt_contracts"]:
        lines.append(f"- {item['persona']}: {item['grade']} ({item['prompt_file']})")
    lines.extend(["", "## Non-Pass Cases"])
    for item in report["evaluations"]:
        if item["grade"] == "pass":
            continue
        lines.append(f"- {item['case_id']} {item['grade']} overall={item['overall']}")
        if item["issues"]:
            lines.append(f"  - issues: {', '.join(item['issues'])}")
    return "\n".join(lines)


def _contains_any(text: str, tokens: list[str]) -> bool:
    lowered = text.lower()
    return any(str(token).lower() in lowered for token in tokens)


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _extract_user_message(user_content: str) -> str:
    for marker in ("[Resolved User Message]", "[Original User Message]", "[User Message]"):
        if marker in user_content:
            return user_content.split(marker, 1)[1].strip().splitlines()[0].strip()
    return user_content.strip().splitlines()[-1].strip()


def _structured_draft_payload(user_content: str) -> dict[str, Any]:
    marker = "[Structured Draft]\n"
    payload_text = user_content.split(marker, 1)[1] if marker in user_content else "{}"
    return json.loads(payload_text)


def _persona_id_from_prompt(system_prompt: str) -> str:
    match = re.search(r"캐릭터는 `([^`]+)`", system_prompt)
    if match:
        return match.group(1)
    for persona_id in PERSONAS:
        if persona_id in system_prompt:
            return persona_id
    return "cheer_sis"


def _profile_markers(profile: dict[str, Any]) -> list[str]:
    markers: list[str] = []
    for key in ("goal", "exercise_level", "lifestyle"):
        value = profile.get(key)
        if value:
            markers.append(str(value))
    for key in ("pain_points", "injury_history", "medical_conditions", "allergies"):
        for value in profile.get(key) or []:
            markers.append(str(value))
    return [marker for marker in markers if marker]


def main() -> None:
    report = asyncio.run(run_suite())
    summary = report["summary"]
    print("[persona-20x20] summary:", json.dumps(summary, ensure_ascii=False))
    print("[persona-20x20] data:", DATA_PATH)
    print("[persona-20x20] report json:", REPORT_JSON_PATH)
    print("[persona-20x20] report md:", REPORT_MD_PATH)
    if summary["fail_count"] or summary["prompt_contract_fail_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
