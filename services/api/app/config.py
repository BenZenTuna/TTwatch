from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Pydantic-settings automatically reads env vars matching field names
    (case-insensitive). All service URLs are configurable for LAN distribution.
    """
    # Database
    DATABASE_URL: str = "postgresql://ttwatch_app:changeme@postgres:5432/ttwatch"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    REDIS_DEDUP_URL: str = "redis://redis:6379/2"
    REDIS_CACHE_URL: str = "redis://redis:6379/3"

    # Qdrant
    QDRANT_URL: str = "http://qdrant:6333"

    # LLM — can point to local Docker service OR remote LAN machine
    LLM_PROVIDER: str = "local"  # "local" or "cloud"
    VLLM_URL: str = "http://vllm:8000/v1"
    LOCAL_MODEL_NAME: str = "Qwen2.5-32B-Instruct-AWQ"

    # Embedder — can point to local Docker service OR remote LAN machine
    EMBEDDER_URL: str = "http://embedder:8001"
    EMBEDDING_DIMENSION: int = 1024  # Qwen3-Embedding-0.6B = 1024, OpenAI large = 3072

    # SearXNG — can point to local Docker service OR remote LAN machine
    SEARXNG_URL: str = "http://searxng:8080"

    # MinIO — can point to local Docker service OR remote LAN machine
    MINIO_URL: str = "http://minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ttwatch-content"

    # Cloud LLM (fallback or primary for GPU-less)
    CLOUD_LLM_PROVIDER: str = "openai"
    CLOUD_LLM_API_KEY: str = ""
    CLOUD_LLM_MODEL: str = "gpt-4o-mini"
    CLOUD_EMBEDDING_PROVIDER: str = "openai"
    CLOUD_EMBEDDING_MODEL: str = "text-embedding-3-large"

    # Auth
    JWT_SECRET: str = "change-me"
    CORS_ORIGINS: str = "http://localhost:3000"  # comma-separated for multiple

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
