from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # Gemini Flash (응답 생성)
    GEMINI_API_KEY: str
    GEMINI_MODEL_NAME: str = "gemini-3.5-flash"
    # Gemini Flash-Lite (의도 분석 · 검색 평가)
    ROUTER_API_KEY: Optional[str] = None
    ROUTER_MODEL_NAME: str = "gemini-3.5-flash"
    # Pinecone/RAG is disabled in the fast demo flow. Keep these optional for
    # legacy scripts or future RAG reactivation.
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_INDEX_NAME: str = "capstone-v2"
    ENABLE_RAG_MEMORY: bool = False
    # WAS
    WAS_BASE_URL: str
    WAS_TIMEOUT: float = 10.0
    INTERNAL_API_KEY: Optional[str] = None
    # Summary
    SUMMARY_TURN_INTERVAL: int = 10  # 턴 카운터 임계값
    MAX_MESSAGES: int = 10            # State에 보관할 최대 메시지 수 (경량화)
    # Checkpoint
    CHECKPOINT_DB_PATH: str = "data/checkpoints.sqlite"
    CHECKPOINT_TTL_HOURS: int = 72    # 오래된 체크포인트 자동 삭제 (시간)
    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # LangChain / LangSmith Tracing
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGCHAIN_API_KEY: Optional[str] = None
    LANGCHAIN_PROJECT: str = "capstone-v2"
    LANGSMITH_TRACING: str = "false"
    LANGSMITH_ENDPOINT: Optional[str] = None
    LANGSMITH_API_KEY: Optional[str] = None
    LANGSMITH_PROJECT: Optional[str] = None
    LANGSMITH_QUALITY_ENABLED: bool = False
    LANGSMITH_SEND_FULL_TEXT: bool = False
    LANGSMITH_MAX_CHILD_RUNS: int = 80
    LANGSMITH_CHILD_EVENT_SAMPLE_RATE: float = 1.0

    model_config = {"env_file": ENV_FILE, "env_file_encoding": "utf-8-sig"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
