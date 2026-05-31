import argparse
import asyncio
import json
import logging
import os
from dotenv import load_dotenv

# Ensure we can import from app
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.clients.embedding import EmbeddingClient
from app.clients.pinecone import PineconeClient
from pinecone import PineconeAsyncio

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest external knowledge into Pinecone.")
    parser.add_argument(
        "--data",
        default=os.path.join(os.path.dirname(__file__), "../data/external_knowledge_v2.json"),
        help="Path to the external knowledge JSON file.",
    )
    parser.add_argument(
        "--reset-external",
        action="store_true",
        help="Delete only the shared external namespace before ingestion.",
    )
    parser.add_argument("--sleep", type=float, default=0.15, help="Delay between embedding requests.")
    return parser.parse_args()


async def ingest(args: argparse.Namespace):
    # 1. API Keys and Clients
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("ROUTER_API_KEY")
    pinecone_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME", "health-coach-ai")

    if not gemini_key or not pinecone_key:
        logger.error("API keys (GEMINI/PINECONE) not found in .env")
        return

    embed_client = EmbeddingClient(api_key=gemini_key)
    pc_core = PineconeAsyncio(api_key=pinecone_key)
    
    # Get index host
    description = await pc_core.describe_index(index_name)
    index = pc_core.IndexAsyncio(host=description.host)
    pc_client = PineconeClient(index=index)

    if args.reset_external:
        logger.warning("Resetting Pinecone namespace '%s' only", PineconeClient.EXTERNAL_NS)
        await index.delete(delete_all=True, namespace=PineconeClient.EXTERNAL_NS)

    # 2. Load Data
    data_path = os.path.abspath(args.data)
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            knowledge_items = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load JSON data: {e}")
        return

    logger.info(f"Starting ingestion of {len(knowledge_items)} items...")

    # 3. Processing
    success_count = 0
    for i, item in enumerate(knowledge_items):
        try:
            text = item['text']
            source = item.get('source_title') or item.get('source') or item.get('url') or "external"
            category = item['category']
            tags = item.get('tags', [])
            extra_metadata = {
                key: value
                for key, value in item.items()
                if key not in {"text", "source", "category", "tags"}
            }

            logger.info(f"[{i+1}/{len(knowledge_items)}] Embedding: {source} ({category})...")
            
            # Use EmbeddingClient
            vector = await embed_client.embed(text)
            
            # Use PineconeClient expansion
            await pc_client.upsert_external(
                vector=vector,
                text=text,
                source=source,
                category=category,
                tags=tags,
                extra_metadata=extra_metadata,
            )
            success_count += 1
            logger.info(f"Successfully upserted: {source}")
            
            # Small sleep to avoid rate limiting if needed
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)
            
        except Exception as e:
            logger.error(f"Error at item {i+1}: {e}")

    logger.info(f"Ingestion complete! Success: {success_count}/{len(knowledge_items)}")
    close_index = getattr(index, "close", None)
    if close_index:
        maybe_awaitable = close_index()
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable
    await pc_core.close()

if __name__ == "__main__":
    asyncio.run(ingest(_parse_args()))
