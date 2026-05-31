import asyncio
import os
from dotenv import load_dotenv
from pinecone import PineconeAsyncio

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.clients.embedding import EmbeddingClient
from app.clients.pinecone import PineconeClient

async def test_search():
    load_dotenv()
    gemini_key = os.getenv("GEMINI_API_KEY")
    pinecone_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME", "health-coach-ai")

    embed_client = EmbeddingClient(api_key=gemini_key)
    pc_core = PineconeAsyncio(api_key=pinecone_key)
    description = await pc_core.describe_index(index_name)
    index = pc_core.IndexAsyncio(host=description.host)
    pc_client = PineconeClient(index=index)

    queries = [
        "스쿼트 자세 알려줘",
        "ENFP를 위한 운동 추천",
        "유산소 심박수 계산법"
    ]

    for q in queries:
        print(f"\n--- 쿼리: {q} ---")
        vec = await embed_client.embed(q)
        results = await pc_client.search_external(vec, top_k=2)
        for i, r in enumerate(results):
            print(f"[{i+1}] Score: {r['score']:.4f} | Source: {r['source']} | Text: {r['text'][:100]}...")

    close_index = getattr(index, "close", None)
    if close_index:
        maybe_awaitable = close_index()
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable
    await pc_core.close()

if __name__ == "__main__":
    asyncio.run(test_search())
