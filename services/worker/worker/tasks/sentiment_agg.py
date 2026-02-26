"""Aggregate article sentiment into periodic sentiment_history snapshots."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article, Cluster, SentimentHistory

logger = logging.getLogger(__name__)


@app.task(name="compute_sentiment_history")
@with_rls_context
def compute_sentiment_history(user_id: str, topic_id: str, session=None):
    """Aggregate per-cluster sentiment into daily sentiment_history snapshots.

    For each cluster, computes the average sentiment_score of articles
    ingested today and upserts a sentiment_history record.

    Uses datetime.now(timezone.utc).date() — NOT date.today() — to ensure
    UTC-consistent date boundaries regardless of server timezone.
    """
    today = datetime.now(timezone.utc).date()

    clusters = session.execute(
        select(Cluster.id, Cluster.keyword).where(Cluster.topic_id == topic_id)
    ).all()

    created_count = 0
    for cluster_id, cluster_keyword in clusters:
        agg = session.execute(
            select(
                func.avg(Article.sentiment_score),
                func.count(Article.id),
            ).where(
                Article.cluster_id == cluster_id,
                Article.sentiment_score.isnot(None),
                func.date(Article.ingested_at) == today,
                Article.is_duplicate == False,
            )
        ).one()

        avg_sentiment, article_count = agg
        if article_count == 0:
            continue

        # Upsert: check if record exists for this cluster+date
        existing = session.execute(
            select(SentimentHistory).where(
                SentimentHistory.user_id == user_id,
                SentimentHistory.cluster_id == cluster_id,
                SentimentHistory.period_start == today,
            )
        ).scalar_one_or_none()

        if existing:
            existing.avg_sentiment = float(avg_sentiment)
            existing.article_count = article_count
            existing.cluster_keyword = cluster_keyword
        else:
            session.add(SentimentHistory(
                user_id=user_id,
                topic_id=topic_id,
                cluster_id=cluster_id,
                cluster_keyword=cluster_keyword,
                period_start=today,
                avg_sentiment=float(avg_sentiment),
                article_count=article_count,
            ))
            created_count += 1

    logger.info(f"Sentiment history: {created_count} new snapshots for topic {topic_id}")
