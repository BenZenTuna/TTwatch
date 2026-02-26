"""Briefing generation and retrieval endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, Briefing, Topic
from app.schemas.topics import BriefingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics/{topic_id}/briefings", response_model=list[BriefingResponse])
async def list_briefings(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List briefings for a topic, most recent first."""
    result = await db.execute(
        select(Briefing).where(
            Briefing.topic_id == topic_id,
            Briefing.user_id == user.id,
        ).order_by(Briefing.generated_at.desc())
    )
    return result.scalars().all()


@router.get("/briefings/{briefing_id}", response_model=BriefingResponse)
async def get_briefing(
    briefing_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single briefing by ID."""
    result = await db.execute(
        select(Briefing).where(
            Briefing.id == briefing_id,
            Briefing.user_id == user.id,
        )
    )
    briefing = result.scalar_one_or_none()
    if not briefing:
        raise HTTPException(404, "Briefing not found")
    return briefing


@router.post("/topics/{topic_id}/briefings/generate", status_code=202)
async def trigger_briefing_generation(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger briefing generation for a topic.

    Sends the task to the Celery worker queue and returns immediately.
    The briefing will appear in GET /topics/{topic_id}/briefings once generated.
    """
    # Verify topic ownership
    result = await db.execute(
        select(Topic).where(
            Topic.id == topic_id,
            Topic.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Topic not found")

    from app.celery_client import celery_app

    task = celery_app.send_task(
        "generate_briefing",
        kwargs={"user_id": str(user.id), "topic_id": str(topic_id)},
    )
    return {"task_id": task.id, "status": "queued"}
