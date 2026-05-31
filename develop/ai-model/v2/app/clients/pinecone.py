"""PineconeClient — VDB 읽기/쓰기 클라이언트.

Layer 4 VDB 설계:
  - vdb_memory:          에피소드 · 추억  (namespace: {user_id}-memory)
  - vdb_user_important:  핵심 팩트 요약  (namespace: {user_id}-important)
  - vdb_external:        외부 지식       (namespace: external)

user_id를 namespace prefix로 사용하여 사용자 간 벡터를 격리한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4


class PineconeClient:
    def __init__(self, index: Any) -> None:
        self._index = index

    # ── namespace 헬퍼 ───────────────────────────────────────────────────────

    @staticmethod
    def _memory_ns(user_id: str) -> str:
        return f"{user_id}-memory"

    @staticmethod
    def _important_ns(user_id: str) -> str:
        return f"{user_id}-important"

    EXTERNAL_NS: str = "external"

    # ── 검색 ─────────────────────────────────────────────────────────────────

    async def search_memory(
        self,
        user_id: str,
        vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict]:
        return await self._search(
            self._memory_ns(user_id),
            vector,
            top_k,
            source="memory",
            metadata_filter=metadata_filter,
        )

    async def search_important(
        self,
        user_id: str,
        vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict]:
        return await self._search(
            self._important_ns(user_id),
            vector,
            top_k,
            source="important",
            metadata_filter=metadata_filter,
        )

    async def search_external(
        self,
        vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict]:
        return await self._search(
            self.EXTERNAL_NS,
            vector,
            top_k,
            source="external",
            metadata_filter=metadata_filter,
        )

    # ── 저장 ─────────────────────────────────────────────────────────────────

    async def upsert_memory(
        self,
        user_id: str,
        vector: list[float],
        text: str,
        emotion_label: str = "",
        intensity: float = 0.0,
    ) -> str:
        metadata = {
            "text": text,
            "source": "memory",
            "timestamp": datetime.utcnow().isoformat(),
            "emotion_label": emotion_label,
            "emotion_intensity": intensity,
        }
        return await self._upsert(self._memory_ns(user_id), vector, metadata)

    async def upsert_important(self, user_id: str, vector: list[float], text: str) -> str:
        metadata = {
            "text": text,
            "source": "important",
            "timestamp": datetime.utcnow().isoformat(),
        }
        return await self._upsert(self._important_ns(user_id), vector, metadata)

    async def upsert_external(
        self,
        vector: list[float],
        text: str,
        source: str,
        category: str,
        tags: list[str] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> str:
        source_title = source
        if extra_metadata and extra_metadata.get("source_title"):
            source_title = str(extra_metadata["source_title"])
        metadata = {
            "text": text,
            "source": "external",
            "source_type": "external_kb",
            "source_title": source_title,
            "category": category,
            "tags": tags or [],
            "timestamp": datetime.utcnow().isoformat(),
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        vector_id = None
        if extra_metadata and extra_metadata.get("kb_id"):
            vector_id = f"external::{extra_metadata['kb_id']}"
        return await self._upsert(self.EXTERNAL_NS, vector, metadata, vector_id=vector_id)

    # ── 삭제 ─────────────────────────────────────────────────────────────────

    async def delete_important(self, user_id: str, ids: list[str]) -> None:
        if not ids:
            return
        await self._index.delete(ids=ids, namespace=self._important_ns(user_id))

    # ── 내부 구현 ─────────────────────────────────────────────────────────────

    async def _search(
        self,
        namespace: str,
        vector: list[float],
        top_k: int,
        source: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict]:
        query_kwargs: dict[str, Any] = {
            "vector": vector,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": True,
        }
        if metadata_filter:
            query_kwargs["filter"] = metadata_filter

        result = await self._index.query(**query_kwargs)
        return [
            {
                "id": m.id,
                "kb_id": m.metadata.get("kb_id", ""),
                "score": m.score,
                "text": m.metadata.get("text", ""),
                "source": m.metadata.get("source", source),
                "source_type": m.metadata.get("source_type", ""),
                "source_title": m.metadata.get("source_title", ""),
                "domain": m.metadata.get("domain", ""),
                "topic": m.metadata.get("topic", ""),
                "category": m.metadata.get("category", ""),
                "tags": m.metadata.get("tags", []),
                "subtopic": m.metadata.get("subtopic", ""),
                "chunk_title": m.metadata.get("chunk_title", ""),
                "evidence_type": m.metadata.get("evidence_type", ""),
                "evidence_rank": m.metadata.get("evidence_rank", ""),
                "population": m.metadata.get("population", ""),
                "use_case": m.metadata.get("use_case", ""),
                "use_cases": m.metadata.get("use_cases", []),
                "profile_targets": m.metadata.get("profile_targets", []),
                "constraints": m.metadata.get("constraints", []),
                "goals": m.metadata.get("goals", []),
                "risk_level": m.metadata.get("risk_level", ""),
                "locale": m.metadata.get("locale", ""),
                "url": m.metadata.get("url", "") or m.metadata.get("source_url", ""),
                "version": m.metadata.get("version", ""),
                "year": m.metadata.get("year", ""),
                "timestamp": m.metadata.get("timestamp", ""),
                "metadata": dict(m.metadata or {}),
            }
            for m in result.matches
        ]

    async def _upsert(
        self,
        namespace: str,
        vector: list[float],
        metadata: dict,
        *,
        vector_id: str | None = None,
    ) -> str:
        vid = vector_id or str(uuid4())
        await self._index.upsert(
            vectors=[{"id": vid, "values": vector, "metadata": metadata}],
            namespace=namespace,
        )
        return vid
