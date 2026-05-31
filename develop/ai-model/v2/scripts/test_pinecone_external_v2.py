"""Live smoke test for the v2 Pinecone external knowledge schema."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pinecone import PineconeAsyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.clients.embedding import EmbeddingClient
from app.clients.pinecone import PineconeClient


ROOT = Path(__file__).resolve().parents[1]


CASES = [
    {
        "case_id": "beginner_knee_workout",
        "query": "초보자 무릎 통증 15분 운동 플랜 스트레칭 유산소",
        "filter": {
            "$and": [
                {"source_type": {"$in": ["external_kb"]}},
                {"domain": {"$in": ["workout"]}},
                {"profile_targets": {"$in": ["beginner"]}},
                {"constraints": {"$in": ["knee_pain"]}},
            ]
        },
    },
    {
        "case_id": "older_adult_workout",
        "query": "65세 이상 초보자 균형 근력 안전 운동",
        "filter": {
            "$and": [
                {"source_type": {"$in": ["external_kb"]}},
                {"domain": {"$in": ["workout"]}},
                {"profile_targets": {"$in": ["older_adult"]}},
            ]
        },
    },
    {
        "case_id": "food_allergy_diet",
        "query": "유제품 알레르기 식단 단백질 대체",
        "filter": {
            "$and": [
                {"source_type": {"$in": ["external_kb"]}},
                {"domain": {"$in": ["diet"]}},
                {"constraints": {"$in": ["food_allergy"]}},
            ]
        },
    },
    {
        "case_id": "glucose_diet",
        "query": "혈당 관리 식단 현미 단백질 채소",
        "filter": {
            "$and": [
                {"source_type": {"$in": ["external_kb"]}},
                {"domain": {"$in": ["diet"]}},
                {"goals": {"$in": ["glucose_control"]}},
            ]
        },
    },
    {
        "case_id": "hypertension_diet",
        "query": "고혈압 나트륨 줄이는 식단",
        "filter": {
            "$and": [
                {"source_type": {"$in": ["external_kb"]}},
                {"domain": {"$in": ["diet"]}},
                {"constraints": {"$in": ["hypertension"]}},
            ]
        },
    },
]


async def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("ROUTER_API_KEY")
    pinecone_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME", "health-coach-ai")
    if not gemini_key or not pinecone_key:
        raise RuntimeError("GEMINI_API_KEY/ROUTER_API_KEY and PINECONE_API_KEY are required")

    embed_client = EmbeddingClient(api_key=gemini_key)
    pc_core = PineconeAsyncio(api_key=pinecone_key)
    description = await pc_core.describe_index(index_name)
    index = pc_core.IndexAsyncio(host=description.host)
    client = PineconeClient(index=index)

    failures: list[str] = []
    for case in CASES:
        vector = await embed_client.embed(case["query"])
        results = await client.search_external(vector, top_k=5, metadata_filter=case["filter"])
        if len(results) < 1:
            failures.append(case["case_id"])
            print(f"[fail] {case['case_id']}: no strict results")
            continue
        top = results[0]
        print(
            f"[pass] {case['case_id']}: {len(results)} results, "
            f"top={top.get('chunk_title')} score={float(top.get('score') or 0.0):.4f}"
        )

    close_index = getattr(index, "close", None)
    if close_index:
        maybe_awaitable = close_index()
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable
    await pc_core.close()
    if failures:
        raise SystemExit(f"external v2 search failed: {failures}")


if __name__ == "__main__":
    asyncio.run(main())
