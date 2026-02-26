"""Article management endpoints."""
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, Article, Entity, EntityArticleMap
from app.schemas.topics import ArticleResponse
from app.schemas.articles import ArticleDetailResponse
from app.schemas.entities import EntityResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics/{topic_id}/articles", response_model=list[ArticleResponse])
async def list_topic_articles(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cluster_id: uuid.UUID | None = Query(default=None),
    is_duplicate: bool | None = Query(default=None),
    published_after: datetime | None = Query(default=None),
    published_before: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List articles for a topic with optional filters."""
    stmt = select(Article).where(
        Article.topic_id == topic_id,
        Article.user_id == user.id,
    )
    if cluster_id is not None:
        stmt = stmt.where(Article.cluster_id == cluster_id)
    if is_duplicate is not None:
        stmt = stmt.where(Article.is_duplicate == is_duplicate)
    if published_after is not None:
        stmt = stmt.where(Article.published_at >= published_after)
    if published_before is not None:
        stmt = stmt.where(Article.published_at <= published_before)

    stmt = stmt.order_by(Article.ingested_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/articles/{article_id}", response_model=ArticleDetailResponse)
async def get_article(
    article_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single article with full details."""
    result = await db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.user_id == user.id,
        )
    )
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(404, "Article not found")
    return article


@router.get("/articles/{article_id}/entities", response_model=list[EntityResponse])
async def list_article_entities(
    article_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List entities linked to an article."""
    # Verify article ownership
    article_result = await db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.user_id == user.id,
        )
    )
    if not article_result.scalar_one_or_none():
        raise HTTPException(404, "Article not found")

    result = await db.execute(
        select(Entity).join(
            EntityArticleMap, Entity.id == EntityArticleMap.entity_id
        ).where(
            EntityArticleMap.article_id == article_id,
            EntityArticleMap.user_id == user.id,
        ).order_by(Entity.name)
    )
    return result.scalars().all()
