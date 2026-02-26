"""Source management endpoints (RSS feeds, web sources)."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, Source, Topic
from app.schemas.sources import SourceCreate, SourceUpdate, SourceResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics/{topic_id}/sources", response_model=list[SourceResponse])
async def list_sources(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all sources for a topic."""
    result = await db.execute(
        select(Source).where(
            Source.topic_id == topic_id,
            Source.user_id == user.id,
        ).order_by(Source.name)
    )
    return result.scalars().all()


@router.post("/topics/{topic_id}/sources", response_model=SourceResponse, status_code=201)
async def add_source(
    topic_id: uuid.UUID,
    req: SourceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a source to a topic."""
    # Verify topic ownership
    topic_result = await db.execute(
        select(Topic).where(
            Topic.id == topic_id,
            Topic.user_id == user.id,
        )
    )
    if not topic_result.scalar_one_or_none():
        raise HTTPException(404, "Topic not found")

    # Check for duplicate URL within topic
    existing = await db.execute(
        select(Source).where(
            Source.user_id == user.id,
            Source.topic_id == topic_id,
            Source.url == req.url,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Source URL already exists for this topic")

    source = Source(
        user_id=user.id,
        topic_id=topic_id,
        name=req.name,
        url=req.url,
        source_type=req.source_type,
        enabled=req.enabled,
        config=req.config,
    )
    db.add(source)
    await db.flush()
    return source


@router.put("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    req: SourceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a source (enable/disable, rename, change config)."""
    result = await db.execute(
        select(Source).where(
            Source.id == source_id,
            Source.user_id == user.id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Source not found")

    for field_name in req.model_fields_set:
        setattr(source, field_name, getattr(req, field_name))
    return source


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a source."""
    result = await db.execute(
        select(Source).where(
            Source.id == source_id,
            Source.user_id == user.id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Source not found")
    await db.delete(source)
