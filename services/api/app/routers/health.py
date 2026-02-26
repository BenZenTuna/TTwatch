import httpx
from fastapi import APIRouter, Request
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/health/services")
async def service_health(request: Request):
    """Extended health check — reports connectivity to all services."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        if settings.VLLM_URL:
            try:
                base = settings.VLLM_URL.replace("/v1", "")
                resp = await client.get(f"{base}/health")
                results["vllm"] = resp.status_code == 200
            except Exception:
                results["vllm"] = False

        if settings.EMBEDDER_URL:
            try:
                resp = await client.get(f"{settings.EMBEDDER_URL}/health")
                results["embedder"] = resp.status_code == 200
            except Exception:
                results["embedder"] = False

        if settings.SEARXNG_URL:
            try:
                resp = await client.get(f"{settings.SEARXNG_URL}/healthz")
                results["searxng"] = resp.status_code == 200
            except Exception:
                results["searxng"] = False

        try:
            resp = await client.get(f"{settings.QDRANT_URL}/healthz")
            results["qdrant"] = resp.status_code == 200
        except Exception:
            results["qdrant"] = False

    # Check PostgreSQL
    try:
        from app.deps import engine
        from sqlalchemy import text as sa_text
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
        results["postgres"] = True
    except Exception:
        results["postgres"] = False

    # Check Redis
    try:
        from app.deps import cache_redis
        await cache_redis.ping()
        results["redis"] = True
    except Exception:
        results["redis"] = False

    results["mode"] = settings.LLM_PROVIDER
    return results
