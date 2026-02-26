"""Named entity endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, Entity, Article, EntityArticleMap
from app.schemas.entities import EntityResponse
from app.schemas.topics import ArticleResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics/{topic_id}/entities", response_model=list[EntityResponse])
async def list_topic_entities(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    entity_type: str | None = Query(default=None, alias="type"),
):
    """List entities for a topic, optionally filtered by type."""
    stmt = select(Entity).where(
        Entity.topic_id == topic_id,
        Entity.user_id == user.id,
    )
    if entity_type is not None:
        stmt = stmt.where(Entity.type == entity_type)

    stmt = stmt.order_by(Entity.name)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/entities/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single entity by ID."""
    result = await db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.user_id == user.id,
        )
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(404, "Entity not found")
    return entity


@router.get("/entities/{entity_id}/articles", response_model=list[ArticleResponse])
async def list_entity_articles(
    entity_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List articles that mention an entity."""
    # Verify entity ownership
    entity_result = await db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.user_id == user.id,
        )
    )
    if not entity_result.scalar_one_or_none():
        raise HTTPException(404, "Entity not found")

    result = await db.execute(
        select(Article).join(
            EntityArticleMap, Article.id == EntityArticleMap.article_id
        ).where(
            EntityArticleMap.entity_id == entity_id,
            EntityArticleMap.user_id == user.id,
        ).order_by(Article.ingested_at.desc()).offset(offset).limit(limit)
    )
    return result.scalars().all()
