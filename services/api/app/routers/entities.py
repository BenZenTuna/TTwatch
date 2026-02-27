"""Named entity endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.deps import get_current_user, get_db
from app.models import User, Entity, Article, EntityArticleMap
from app.schemas.entities import (
    EntityResponse,
    EntityGraphResponse,
    EntityNodeResponse,
    EntityEdgeResponse,
)
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


@router.get("/topics/{topic_id}/entity-graph", response_model=EntityGraphResponse)
async def get_entity_graph(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    min_articles: int = Query(default=1, ge=1, description="Minimum article count to include an entity"),
    min_cooccurrence: int = Query(default=2, ge=1, description="Minimum shared articles for an edge"),
):
    """Return entity nodes with article counts and co-occurrence edges for a topic.

    Edges represent entities that appear in the same article. Edge weight
    is the number of articles two entities co-occur in. Only edges with
    weight >= min_cooccurrence are returned.
    """
    # Entities with their article counts
    entity_rows = (await db.execute(
        select(
            Entity.id,
            Entity.name,
            Entity.type,
            func.count(EntityArticleMap.article_id).label("article_count"),
        )
        .join(EntityArticleMap, Entity.id == EntityArticleMap.entity_id)
        .where(
            Entity.topic_id == topic_id,
            Entity.user_id == user.id,
        )
        .group_by(Entity.id, Entity.name, Entity.type)
        .having(func.count(EntityArticleMap.article_id) >= min_articles)
    )).all()

    entities = [
        EntityNodeResponse(
            id=row.id, name=row.name, type=row.type, article_count=row.article_count,
        )
        for row in entity_rows
    ]

    entity_ids = {row.id for row in entity_rows}
    if len(entity_ids) < 2:
        return EntityGraphResponse(entities=entities, edges=[])

    # Co-occurrence edges via self-join on entity_article_map
    eam1 = aliased(EntityArticleMap)
    eam2 = aliased(EntityArticleMap)

    edge_rows = (await db.execute(
        select(
            eam1.entity_id.label("source"),
            eam2.entity_id.label("target"),
            func.count().label("weight"),
        )
        .join(eam2, and_(
            eam1.article_id == eam2.article_id,
            eam1.entity_id < eam2.entity_id,
        ))
        .where(
            eam1.user_id == user.id,
            eam1.entity_id.in_(entity_ids),
            eam2.entity_id.in_(entity_ids),
        )
        .group_by(eam1.entity_id, eam2.entity_id)
        .having(func.count() >= min_cooccurrence)
    )).all()

    edges = [
        EntityEdgeResponse(source=row.source, target=row.target, weight=row.weight)
        for row in edge_rows
    ]

    return EntityGraphResponse(entities=entities, edges=edges)


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
