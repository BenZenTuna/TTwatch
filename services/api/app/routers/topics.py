"""CRUD operations for intelligence topics."""
import json
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db, cache_redis
from app.models import User, Topic, Article, Cluster
from app.schemas.topics import TopicCreate, TopicUpdate, TopicResponse, ClusterResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics", response_model=list[TopicResponse])
async def list_topics(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all topics for the current user."""
    result = await db.execute(
        select(Topic).where(Topic.user_id == user.id).order_by(Topic.created_at.desc())
    )
    return result.scalars().all()


@router.post("/topics", response_model=TopicResponse, status_code=201)
async def create_topic(
    req: TopicCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new topic for the current user."""
    # Enforce topic limit
    count = await db.execute(
        select(func.count(Topic.id)).where(Topic.user_id == user.id)
    )
    if count.scalar() >= user.max_topics:
        raise HTTPException(403, f"Topic limit reached ({user.max_topics})")

    topic = Topic(
        user_id=user.id,
        name=req.name,
        icon=req.icon,
        config=req.config,
        refresh_interval_minutes=req.refresh_interval_minutes,
    )
    db.add(topic)
    await db.commit()

    # Dispatch LLM query generation → which then triggers SearXNG searches.
    # Must commit first so the worker can read the topic from the database.
    from app.celery_client import celery_app
    celery_app.send_task(
        "generate_search_queries",
        args=[str(user.id), str(topic.id)],
    )

    # Set initial search status so frontend shows progress immediately
    now = datetime.now(timezone.utc).isoformat()
    await cache_redis.setex(
        f"ttwatch:search_status:{topic.id}", 3600,
        json.dumps({
            "status": "generating_queries",
            "started_at": now,
            "user_id": str(user.id),
        })
    )
    await cache_redis.setex(
        f"ttwatch:search_progress:{topic.id}:started_at", 7200, now
    )

    return topic


@router.get("/topics/{topic_id}", response_model=TopicResponse)
async def get_topic(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single topic by ID."""
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")
    return topic


@router.put("/topics/{topic_id}", response_model=TopicResponse)
async def update_topic(
    topic_id: uuid.UUID,
    req: TopicUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing topic. Supports partial updates — only provided fields are changed.

    Uses Pydantic's model_fields_set to distinguish between "field not provided"
    (not in JSON body) and "field explicitly set to null" ({"icon": null}).
    This enables clearing optional fields like icon.
    """
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")

    # Only update fields that were explicitly included in the request body.
    # model_fields_set contains field names the client actually sent.
    for field_name in req.model_fields_set:
        if field_name == "config" and req.config is not None:
            # Merge config dicts so updating search_queries doesn't wipe search_terms
            merged = dict(topic.config or {})
            merged.update(req.config)
            topic.config = merged
        else:
            setattr(topic, field_name, getattr(req, field_name))
    return topic


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a topic and all associated data.

    NOTE: This only deletes PostgreSQL data. Qdrant vectors for this topic's
    articles will be cleaned up by the daily cleanup_orphaned_qdrant_points task.
    """
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")
    await db.delete(topic)


@router.post("/topics/{topic_id}/search", status_code=202)
async def trigger_topic_search(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a new search for a topic. Rate-limited to once per 5 minutes."""
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")

    lock_key = f"ttwatch:search_lock:{topic_id}"
    if await cache_redis.exists(lock_key):
        return JSONResponse(
            status_code=429,
            content={"detail": "Search recently triggered. Please wait 5 minutes between searches."},
        )

    await cache_redis.setex(lock_key, 300, "1")

    # Set search status immediately
    now = datetime.now(timezone.utc).isoformat()
    await cache_redis.setex(
        f"ttwatch:search_status:{topic_id}", 3600,
        json.dumps({
            "status": "searching",
            "started_at": now,
            "user_id": str(user.id),
        })
    )
    await cache_redis.setex(
        f"ttwatch:search_progress:{topic_id}:started_at", 7200, now
    )

    from app.celery_client import celery_app
    celery_app.send_task(
        "run_topic_search",
        args=[str(user.id), str(topic_id)],
    )

    return {"status": "search_dispatched", "topic_id": str(topic_id)}


@router.get("/topics/{topic_id}/search-status")
async def get_topic_search_status(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
):
    """Get the current search status for a topic.

    Returns a unified response combining search_status and search_progress keys.
    Tracks all phases: generating_queries → searching → processing → completed.
    """
    progress_prefix = f"ttwatch:search_progress:{topic_id}"
    proc_prefix = f"ttwatch:processing:{topic_id}"

    raw = await cache_redis.get(f"ttwatch:search_status:{topic_id}")
    if raw:
        data = json.loads(raw)
        # Cross-tenant check
        if data.get("user_id") != str(user.id):
            return {"status": "idle"}
    else:
        data = None

    # Read progress counters
    queries_total_raw = await cache_redis.get(f"{progress_prefix}:queries_total")
    queries_completed_raw = await cache_redis.get(f"{progress_prefix}:queries_completed")
    ingested_raw = await cache_redis.get(f"{progress_prefix}:ingested")
    tasks_completed_raw = await cache_redis.get(f"{progress_prefix}:tasks_completed")
    started_at_raw = await cache_redis.get(f"{progress_prefix}:started_at")
    phase_raw = await cache_redis.get(f"{proc_prefix}:phase")
    expected_raw = await cache_redis.get(f"{proc_prefix}:expected")

    queries_total = int(queries_total_raw) if queries_total_raw else None
    queries_completed = int(queries_completed_raw) if queries_completed_raw else None
    ingested = int(ingested_raw) if ingested_raw else None
    tasks_completed = int(tasks_completed_raw) if tasks_completed_raw else None
    started_at = started_at_raw.decode() if started_at_raw else None
    phase = phase_raw.decode() if phase_raw else None
    articles_found = int(expected_raw) if expected_raw else None

    # If status key expired but progress counters exist, infer "processing"
    if data is None:
        if tasks_completed is not None and tasks_completed > 0:
            data = {"status": "processing"}
        else:
            return {"status": "idle"}

    status = data.get("status", "idle")

    # If status is "processing" and clustering is complete, transition to "completed"
    if status == "processing" and phase == "complete":
        status = "completed"

    response: dict = {"status": status}

    if started_at:
        response["started_at"] = started_at
    elif "started_at" in data:
        response["started_at"] = data["started_at"]

    if "completed_at" in data:
        response["completed_at"] = data["completed_at"]
    if "error" in data:
        response["error"] = data["error"]

    # Add articles_found from either status blob or expected counter
    if "articles_found" in data:
        response["articles_found"] = data["articles_found"]
    elif articles_found is not None:
        response["articles_found"] = articles_found

    # Add progress counters
    if queries_total is not None:
        response["queries_total"] = queries_total
    if queries_completed is not None:
        response["queries_completed"] = queries_completed
    if ingested is not None:
        response["articles_ingested"] = ingested
    if tasks_completed is not None:
        response["tasks_completed"] = tasks_completed
        af = response.get("articles_found") or articles_found or 0
        response["tasks_total_estimate"] = af * 5

    return response


@router.get("/topics/{topic_id}/processing-status")
async def get_processing_status(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get real-time processing progress for a topic.

    Combines Redis counters (fast, real-time) with DB cluster count.
    Phase transitions: ingesting → processing → clustering → complete → idle.
    """
    proc_prefix = f"ttwatch:processing:{topic_id}"

    # Read all Redis counters in one go
    phase_raw = await cache_redis.get(f"{proc_prefix}:phase")
    expected_raw = await cache_redis.get(f"{proc_prefix}:expected")
    embedded_raw = await cache_redis.get(f"{proc_prefix}:embedded")
    summarized_raw = await cache_redis.get(f"{proc_prefix}:summarized")
    sentiment_raw = await cache_redis.get(f"{proc_prefix}:sentiment")
    relevance_raw = await cache_redis.get(f"{proc_prefix}:relevance")
    cluster_count_raw = await cache_redis.get(f"{proc_prefix}:cluster_count")

    phase = phase_raw.decode() if phase_raw else None
    expected = int(expected_raw) if expected_raw else 0
    embedded = int(embedded_raw) if embedded_raw else 0
    summarized = int(summarized_raw) if summarized_raw else 0
    sentiment = int(sentiment_raw) if sentiment_raw else 0
    relevance = int(relevance_raw) if relevance_raw else 0
    cluster_count = int(cluster_count_raw) if cluster_count_raw else 0

    # If no phase in Redis, check if clusters exist (idle vs never-processed)
    if not phase:
        result = await db.execute(
            select(func.count(Cluster.id)).where(
                Cluster.topic_id == topic_id,
                Cluster.user_id == user.id,
            )
        )
        db_cluster_count = result.scalar() or 0
        return {
            "phase": "idle",
            "total_articles": 0,
            "embedded": 0,
            "summarized": 0,
            "sentiment": 0,
            "relevance": 0,
            "clustered": 1 if db_cluster_count > 0 else 0,
            "cluster_count": db_cluster_count,
        }

    # For "complete" phase, get actual cluster count from DB if not in Redis
    if phase == "complete" and cluster_count == 0:
        result = await db.execute(
            select(func.count(Cluster.id)).where(
                Cluster.topic_id == topic_id,
                Cluster.user_id == user.id,
            )
        )
        cluster_count = result.scalar() or 0

    return {
        "phase": phase,
        "total_articles": expected,
        "embedded": embedded,
        "summarized": summarized,
        "sentiment": sentiment,
        "relevance": relevance,
        "clustered": 1 if phase == "complete" else 0,
        "cluster_count": cluster_count,
    }


@router.get("/topics/{topic_id}/clusters", response_model=list[ClusterResponse])
async def list_clusters(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List clusters for a topic, ordered by trend score."""
    result = await db.execute(
        select(Cluster).where(
            Cluster.topic_id == topic_id,
            Cluster.user_id == user.id,
        ).order_by(Cluster.trend_score.desc())
    )
    return result.scalars().all()
