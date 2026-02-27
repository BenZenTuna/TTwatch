"""Service version checker — queries upstream registries for latest releases."""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

REDIS_KEY = "ttwatch:version_check"
CACHE_TTL = 86400  # 24 hours


@dataclass
class TrackedService:
    name: str
    env_var: str
    default: str
    source_type: str  # "dockerhub", "github", "huggingface"
    source_ref: str  # e.g. "vllm/vllm-openai" or "qdrant/qdrant"
    changelog_url: str


TRACKED_SERVICES = [
    TrackedService(
        name="vLLM",
        env_var="VLLM_VERSION",
        default="v0.16.0",
        source_type="github",
        source_ref="vllm-project/vllm",
        changelog_url="https://github.com/vllm-project/vllm/releases",
    ),
    TrackedService(
        name="Qdrant",
        env_var="QDRANT_VERSION",
        default="v1.12.1",
        source_type="github",
        source_ref="qdrant/qdrant",
        changelog_url="https://github.com/qdrant/qdrant/releases",
    ),
    TrackedService(
        name="SearXNG",
        env_var="SEARXNG_VERSION",
        default="latest",
        source_type="dockerhub",
        source_ref="searxng/searxng",
        changelog_url="https://github.com/searxng/searxng/releases",
    ),
    TrackedService(
        name="PostgreSQL",
        env_var="POSTGRES_VERSION",
        default="16",
        source_type="dockerhub",
        source_ref="library/postgres",
        changelog_url="https://www.postgresql.org/docs/release/",
    ),
    TrackedService(
        name="Redis",
        env_var="REDIS_VERSION",
        default="7-alpine",
        source_type="dockerhub",
        source_ref="library/redis",
        changelog_url="https://github.com/redis/redis/releases",
    ),
    TrackedService(
        name="MinIO",
        env_var="MINIO_VERSION",
        default="RELEASE.2024-11-07T00-52-20Z",
        source_type="github",
        source_ref="minio/minio",
        changelog_url="https://github.com/minio/minio/releases",
    ),
    TrackedService(
        name="Qwen3 Model",
        env_var="LOCAL_MODEL_NAME",
        default="Qwen3-32B-AWQ",
        source_type="huggingface",
        source_ref="Qwen/Qwen3-32B-AWQ",
        changelog_url="https://huggingface.co/Qwen/Qwen3-32B-AWQ",
    ),
    TrackedService(
        name="Embedding Model",
        env_var="EMBEDDING_MODEL_NAME",
        default="Qwen/Qwen3-Embedding-0.6B",
        source_type="huggingface",
        source_ref="Qwen/Qwen3-Embedding-0.6B",
        changelog_url="https://huggingface.co/Qwen/Qwen3-Embedding-0.6B",
    ),
]


def _get_current_version(svc: TrackedService) -> str:
    return os.environ.get(svc.env_var, svc.default)


def check_dockerhub(client: httpx.Client, source_ref: str) -> str | None:
    """Get the most recent non-latest tag from Docker Hub."""
    url = f"https://hub.docker.com/v2/repositories/{source_ref}/tags"
    try:
        resp = client.get(url, params={"page_size": 25, "ordering": "last_updated"})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for tag in results:
            name = tag.get("name", "")
            if name and name != "latest":
                return name
    except Exception as e:
        logger.warning(f"DockerHub check failed for {source_ref}: {e}")
    return None


def check_github(client: httpx.Client, source_ref: str) -> str | None:
    """Get the latest release tag from GitHub."""
    url = f"https://api.github.com/repos/{source_ref}/releases/latest"
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json().get("tag_name")
    except Exception as e:
        logger.warning(f"GitHub check failed for {source_ref}: {e}")
    return None


def check_huggingface(client: httpx.Client, source_ref: str) -> str | None:
    """Get the lastModified date from HuggingFace model API."""
    url = f"https://huggingface.co/api/models/{source_ref}"
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json().get("lastModified")
    except Exception as e:
        logger.warning(f"HuggingFace check failed for {source_ref}: {e}")
    return None


CHECKERS = {
    "dockerhub": check_dockerhub,
    "github": check_github,
    "huggingface": check_huggingface,
}


def check_all_versions() -> dict:
    """Run all version checks and return results dict."""
    results = []
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        for svc in TRACKED_SERVICES:
            current = _get_current_version(svc)
            checker = CHECKERS.get(svc.source_type)
            latest = checker(client, svc.source_ref) if checker else None

            has_update = False
            if latest and current != "latest":
                has_update = latest != current

            results.append({
                "name": svc.name,
                "env_var": svc.env_var,
                "current": current,
                "latest": latest,
                "has_update": has_update,
                "source_type": svc.source_type,
                "changelog_url": svc.changelog_url,
            })

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": results,
    }


async def check_and_cache_versions(cache_redis) -> dict:
    """Run all checks and cache the result in Redis."""
    result = check_all_versions()
    await cache_redis.set(REDIS_KEY, json.dumps(result), ex=CACHE_TTL)
    return result


async def get_cached_versions(cache_redis) -> dict | None:
    """Read cached version check result from Redis."""
    raw = await cache_redis.get(REDIS_KEY)
    if raw:
        return json.loads(raw)
    return None
