"""Sentiment history and analysis endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, SentimentHistory, Cluster
from app.schemas.sentiment import SentimentPointResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics/{topic_id}/sentiment", response_model=list[SentimentPointResponse])
async def get_sentiment_overview(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent sentiment data point per cluster for a topic.

    Returns one row per cluster, representing the latest sentiment snapshot.
    """
    # Subquery: max period_start per cluster for this topic
    latest = (
        select(
            SentimentHistory.cluster_id,
            func.max(SentimentHistory.period_start).label("max_period"),
        )
        .where(
            SentimentHistory.topic_id == topic_id,
            SentimentHistory.user_id == user.id,
        )
        .group_by(SentimentHistory.cluster_id)
        .subquery()
    )

    result = await db.execute(
        select(SentimentHistory)
        .join(
            latest,
            (SentimentHistory.cluster_id == latest.c.cluster_id)
            & (SentimentHistory.period_start == latest.c.max_period),
        )
        .where(
            SentimentHistory.topic_id == topic_id,
            SentimentHistory.user_id == user.id,
        )
        .order_by(SentimentHistory.cluster_keyword)
    )
    return result.scalars().all()


@router.get("/topics/{topic_id}/sentiment/history", response_model=list[SentimentPointResponse])
async def get_sentiment_history(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cluster_keyword: str | None = Query(default=None),
    limit: int = Query(default=90, le=365),
):
    """Get sentiment history time series for a topic.

    Optionally filter by cluster_keyword for recluster-proof history.
    Without a filter, returns all history rows for the topic.
    """
    stmt = select(SentimentHistory).where(
        SentimentHistory.topic_id == topic_id,
        SentimentHistory.user_id == user.id,
    )
    if cluster_keyword is not None:
        stmt = stmt.where(SentimentHistory.cluster_keyword == cluster_keyword)

    stmt = stmt.order_by(SentimentHistory.period_start.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()
