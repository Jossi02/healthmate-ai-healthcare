"""Dependency container injected into graph nodes."""
from dataclasses import dataclass
from typing import Optional

from app.clients.embedding import EmbeddingClient
from app.clients.gemini import GeminiClient
from app.clients.pinecone import PineconeClient
from app.clients.was import WASClient
from app.core.profile_sync import ProfileSyncTracker
from app.core.trace_store import TraceStore


@dataclass
class NodeDeps:
    gemini: GeminiClient
    router: GeminiClient
    was: WASClient
    pinecone: Optional[PineconeClient]
    embed: Optional[EmbeddingClient]
    profile_sync: ProfileSyncTracker
    trace: TraceStore
