"""Cluster management endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, Cluster, Article
from app.schemas.topics import ClusterResponse, ArticleResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/clusters/{cluster_id}", response_model=ClusterResponse)
async def get_cluster(
    cluster_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single cluster by ID."""
    result = await db.execute(
        select(Cluster).where(
            Cluster.id == cluster_id,
            Cluster.user_id == user.id,
        )
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(404, "Cluster not found")
    return cluster


@router.get("/clusters/{cluster_id}/articles", response_model=list[ArticleResponse])
async def list_cluster_articles(
    cluster_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List articles in a cluster, paginated."""
    # Verify cluster ownership
    cluster_result = await db.execute(
        select(Cluster).where(
            Cluster.id == cluster_id,
            Cluster.user_id == user.id,
        )
    )
    if not cluster_result.scalar_one_or_none():
        raise HTTPException(404, "Cluster not found")

    result = await db.execute(
        select(Article).where(
            Article.cluster_id == cluster_id,
            Article.user_id == user.id,
        ).order_by(Article.ingested_at.desc()).offset(offset).limit(limit)
    )
    return result.scalars().all()
