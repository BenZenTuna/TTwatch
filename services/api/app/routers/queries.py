"""Saved query management endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, SavedQuery, Topic
from app.schemas.queries import SavedQueryCreate, SavedQueryResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics/{topic_id}/queries", response_model=list[SavedQueryResponse])
async def list_saved_queries(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List saved queries for a topic."""
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.topic_id == topic_id,
            SavedQuery.user_id == user.id,
        ).order_by(SavedQuery.created_at.desc())
    )
    return result.scalars().all()


@router.post("/topics/{topic_id}/queries", response_model=SavedQueryResponse, status_code=201)
async def create_saved_query(
    topic_id: uuid.UUID,
    req: SavedQueryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a query for a topic."""
    # Verify topic ownership
    topic_result = await db.execute(
        select(Topic).where(
            Topic.id == topic_id,
            Topic.user_id == user.id,
        )
    )
    if not topic_result.scalar_one_or_none():
        raise HTTPException(404, "Topic not found")

    query = SavedQuery(
        user_id=user.id,
        topic_id=topic_id,
        query_text=req.query_text,
        schedule=req.schedule,
    )
    db.add(query)
    await db.flush()
    return query


@router.delete("/queries/{query_id}", status_code=204)
async def delete_saved_query(
    query_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved query."""
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == query_id,
            SavedQuery.user_id == user.id,
        )
    )
    query = result.scalar_one_or_none()
    if not query:
        raise HTTPException(404, "Saved query not found")
    await db.delete(query)
