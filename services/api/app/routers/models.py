"""Model management API — status, task routing configuration."""
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db, get_current_user
from app.models import User, LlmTaskConfig

router = APIRouter()

# ── Task category metadata ──

TASK_CATEGORIES = [
    {
        "task_category": "summarization",
        "display_name": "Article Summarization",
        "description": "Generate 2-sentence article summaries",
        "default_target": "fast",
        "recommend_primary": False,
    },
    {
        "task_category": "sentiment",
        "display_name": "Sentiment Classification",
        "description": "Score article sentiment from -1.0 to 1.0",
        "default_target": "fast",
        "recommend_primary": False,
    },
    {
        "task_category": "relevance",
        "display_name": "Relevance Scoring",
        "description": "Rate article relevance to monitoring topic",
        "default_target": "fast",
        "recommend_primary": False,
    },
    {
        "task_category": "entity_extraction",
        "display_name": "Entity Extraction",
        "description": "Extract named entities (people, orgs, products)",
        "default_target": "fast",
        "recommend_primary": False,
    },
    {
        "task_category": "ticker_resolution",
        "display_name": "Ticker Resolution",
        "description": "Resolve entity names to stock/crypto symbols",
        "default_target": "fast",
        "recommend_primary": False,
    },
    {
        "task_category": "briefing",
        "display_name": "Briefing Generation",
        "description": "Generate executive-style intelligence briefings",
        "default_target": "fast",
        "recommend_primary": True,
    },
    {
        "task_category": "investment_analysis",
        "display_name": "Investment Analysis",
        "description": "Generate investment research and recommendations",
        "default_target": "fast",
        "recommend_primary": True,
    },
    {
        "task_category": "coverage_gaps",
        "display_name": "Coverage Gap Detection",
        "description": "Identify blind spots in topic coverage",
        "default_target": "fast",
        "recommend_primary": True,
    },
    {
        "task_category": "search_planning",
        "display_name": "Search Query Planning",
        "description": "Decompose topics into effective search queries",
        "default_target": "fast",
        "recommend_primary": True,
    },
    {
        "task_category": "correlation",
        "display_name": "Correlation Detection",
        "description": "Detect news-price correlation signals",
        "default_target": "fast",
        "recommend_primary": False,
    },
]

VALID_TARGETS = {"primary", "fast", "auto"}
CATEGORY_NAMES = {c["task_category"] for c in TASK_CATEGORIES}


# ── Schemas ──

class ModelInfo(BaseModel):
    id: str
    name: str
    url: str
    status: str
    type: str
    description: str


class ModelStatusResponse(BaseModel):
    models: list[ModelInfo]
    gpu_mode: str
    provider: str


class TaskRoutingEntry(BaseModel):
    task_category: str
    display_name: str
    description: str
    model_target: str
    is_default: bool
    recommend_primary: bool


class TaskRoutingResponse(BaseModel):
    routing: list[TaskRoutingEntry]


class TaskRoutingChange(BaseModel):
    task_category: str
    model_target: str


class TaskRoutingUpdateRequest(BaseModel):
    changes: list[TaskRoutingChange]


# ── Endpoints ──

@router.get("/models/status", response_model=ModelStatusResponse)
async def get_model_status(user: User = Depends(get_current_user)):
    """Return current status of all LLM services."""
    models = []

    async with httpx.AsyncClient(timeout=3.0) as client:
        # Primary model
        if settings.VLLM_URL:
            status = await _check_vllm_status(client, settings.VLLM_URL)
            models.append(ModelInfo(
                id="primary",
                name=settings.LOCAL_MODEL_NAME,
                url=settings.VLLM_URL,
                status=status,
                type="reasoning",
                description=(
                    "Large reasoning model — best for complex analysis, briefings, "
                    "and investment research. Slower but more capable."
                ),
            ))

        # Fast model
        if settings.VLLM_FAST_URL:
            status = await _check_vllm_status(client, settings.VLLM_FAST_URL)
            models.append(ModelInfo(
                id="fast",
                name=settings.FAST_MODEL_NAME,
                url=settings.VLLM_FAST_URL,
                status=status,
                type="classification",
                description=(
                    "Fast model — optimized for sentiment, relevance, entity extraction, "
                    "and summaries. 5-10x faster. Default for all tasks."
                ),
            ))

    gpu_mode = "cloud" if settings.LLM_PROVIDER == "cloud" else "local"
    return ModelStatusResponse(
        models=models,
        gpu_mode=gpu_mode,
        provider=settings.LLM_PROVIDER,
    )


@router.get("/models/task-routing", response_model=TaskRoutingResponse)
async def get_task_routing(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return user's task routing configuration with defaults."""
    rows = await db.execute(
        select(LlmTaskConfig).where(LlmTaskConfig.user_id == user.id)
    )
    user_configs = {r.task_category: r.model_target for r in rows.scalars()}

    routing = []
    for cat in TASK_CATEGORIES:
        key = cat["task_category"]
        if key in user_configs:
            target = user_configs[key]
            is_default = False
        else:
            target = cat["default_target"]
            is_default = True

        routing.append(TaskRoutingEntry(
            task_category=key,
            display_name=cat["display_name"],
            description=cat["description"],
            model_target=target,
            is_default=is_default,
            recommend_primary=cat["recommend_primary"],
        ))

    return TaskRoutingResponse(routing=routing)


@router.put("/models/task-routing", response_model=TaskRoutingResponse)
async def update_task_routing(
    body: TaskRoutingUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update task routing configuration (upsert)."""
    for change in body.changes:
        if change.task_category not in CATEGORY_NAMES:
            from fastapi import HTTPException
            raise HTTPException(400, f"Unknown task category: {change.task_category}")
        if change.model_target not in VALID_TARGETS:
            from fastapi import HTTPException
            raise HTTPException(400, f"Invalid model_target: {change.model_target}. Must be one of: {VALID_TARGETS}")

        existing = await db.execute(
            select(LlmTaskConfig).where(
                LlmTaskConfig.user_id == user.id,
                LlmTaskConfig.task_category == change.task_category,
            )
        )
        row = existing.scalar_one_or_none()

        if row:
            row.model_target = change.model_target
            row.updated_at = datetime.now(timezone.utc)
        else:
            db.add(LlmTaskConfig(
                user_id=user.id,
                task_category=change.task_category,
                model_target=change.model_target,
            ))

    # Return updated routing
    return await get_task_routing(user=user, db=db)


async def _check_vllm_status(client: httpx.AsyncClient, url: str) -> str:
    """Check vLLM instance status via /v1/models endpoint."""
    try:
        base = url.replace("/v1", "")
        resp = await client.get(f"{base}/health")
        return "online" if resp.status_code == 200 else "offline"
    except httpx.ConnectError:
        return "offline"
    except httpx.TimeoutException:
        return "loading"
    except Exception:
        return "offline"
