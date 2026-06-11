"""피드백 루프 — Layer 3 피드백 루프 및 능동형 메모리 매니저 구현.

응답 출력 후 백그라운드에서 실행:
1. should_save_episode=True → vdb_memory 저장 (에피소드)
2. 능동형 메모리 처리 (Active Memory Manager) → vdb_user_important 갱신
3. 감정 이력 저장 (emotion 추이 로깅)

FastAPI BackgroundTasks에서 호출되는 함수로 구현.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.graph.deps import NodeDeps
from app.schemas.llm_responses import MemoryManagerResponse

logger = logging.getLogger(__name__)

_MEM_MANAGER_PROMPT = """
당신은 능동적 메모리 관리자입니다.
새로운 대화 내용에서 사용자의 신체 스펙(체중 등), 취향(알레르기, 선호 음식), 병력(부상 이력), 목표 등 영구적으로 기억할 만한 핵심 팩트가 등장했는지 판단하세요.

만약 새 팩트가 없다면 has_changes=false 로 응답하세요.
새 팩트가 있다면 아래 제공된 기존 팩트 목록(ID 포함)을 확인하고,
새 팩트가 기존 정보와 상충하거나 기존 정보를 대체해야 한다면 UPDATE(기존 삭제 후 삽입) 또는 DELETE(단순 삭제)를 지시하세요. 
기존에 없던 완전히 새로운 팩트라면 ADD를 지시하세요.

[기존 팩트 목록]
{existing_facts}
"""

async def execute_feedback(
    deps: NodeDeps,
    user_id: str,
    user_message: str,
    response: str,
    should_save_episode: bool,
    emotion_label: str,
    emotion_intensity: float,
) -> None:
    if deps.embed is None or deps.pinecone is None:
        logger.info(
            "EMOTION_LOG | user_id=%s | label=%s | intensity=%.2f | memory=disabled",
            user_id,
            emotion_label,
            emotion_intensity,
        )
        return

    """피드백 루프 및 능동 메모리 매니저 실행."""

    # ── 1. should_save_episode (감정 에피소드 저장) ─────────────────────────────
    if should_save_episode:
        episode_text = (
            f"[{datetime.utcnow().strftime('%Y-%m-%d')}] "
            f"사용자: {user_message[:200]} | "
            f"감정: {emotion_label} ({emotion_intensity:.1f})"
        )
        try:
            vec = await deps.embed.embed(episode_text)
            await deps.pinecone.upsert_memory(
                user_id=user_id,
                vector=vec,
                text=episode_text,
                emotion_label=emotion_label,
                intensity=emotion_intensity,
            )
            logger.info("에피소드 vdb_memory 저장 완료: user_id=%s", user_id)
        except Exception as e:
            logger.warning("에피소드 저장 실패 (무시): %s", e)

    # ── 2. Active Memory Manager (능동 팩트 갱신) ──────────────────────────────
    try:
        # 메시지에서 관련 팩트를 찾기 위해 단순 유저 메시지로 검색 후보군 5개 도출
        q_vec = await deps.embed.embed(user_message)
        existing_mems = await deps.pinecone.search_important(user_id, q_vec, top_k=5)
        
        facts_text = "\n".join([f"- ID: {m['id']}, 내용: {m['text']}" for m in existing_mems]) if existing_mems else "(저장된 기존 팩트가 없습니다)"
        
        sys_prompt = _MEM_MANAGER_PROMPT.replace("{existing_facts}", facts_text)
        user_prompt = f"새로운 대화:\n[사용자] {user_message}\n\n[위 규칙에 따라 메모리 DB 변경 작업을 JSON으로 반환하세요.]"
        
        raw_resp = await deps.router.generate(
            system_prompt=sys_prompt,
            user_content=user_prompt,
            response_schema=MemoryManagerResponse
        )
        mem_res = MemoryManagerResponse.model_validate_json(raw_resp)
        
        if mem_res.has_changes:
            ops = mem_res.operations
            for op in ops:
                if op.action == "ADD":
                    n_vec = await deps.embed.embed(op.text)
                    await deps.pinecone.upsert_important(user_id, n_vec, op.text)
                elif op.action == "UPDATE":
                    if op.fact_id:
                        await deps.pinecone.delete_important(user_id, [op.fact_id])
                    n_vec = await deps.embed.embed(op.text)
                    await deps.pinecone.upsert_important(user_id, n_vec, op.text)
                elif op.action == "DELETE":
                    if op.fact_id:
                        await deps.pinecone.delete_important(user_id, [op.fact_id])
            
            if ops:
                logger.info("ActiveMemoryManager: %d개의 메모리 오퍼레이션 처리 완료 (user_id=%s)", len(ops), user_id)
    except Exception as e:
        logger.warning("ActiveMemory 처리 중 에러: %s", e)

    # ── 3. 감정 이력 저장 (현재는 로그 대체) ──────────────────────────────────
    logger.info(
        "EMOTION_LOG | user_id=%s | label=%s | intensity=%.2f",
        user_id,
        emotion_label,
        emotion_intensity,
    )
