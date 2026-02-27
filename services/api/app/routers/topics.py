"""CRUD operations for intelligence topics."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
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
    await db.flush()

    # Dispatch LLM query generation → which then triggers SearXNG searches
    from app.celery_client import celery_app
    celery_app.send_task(
        "generate_search_queries",
        args=[str(user.id), str(topic.id)],
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
