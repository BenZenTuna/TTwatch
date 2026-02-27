"""Aggregate article sentiment into periodic sentiment_history snapshots."""
import logging

from sqlalchemy import select, func, cast, Date

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article, Cluster, SentimentHistory

logger = logging.getLogger(__name__)


@app.task(name="compute_sentiment_history")
@with_rls_context
def compute_sentiment_history(user_id: str, topic_id: str, session=None):
    """Aggregate per-cluster sentiment into daily sentiment_history snapshots.

    For each cluster, groups all non-duplicate articles with sentiment scores
    by ingestion date, then upserts a sentiment_history record per
    (cluster, date) pair. This backfill approach means:

    - Missed runs are self-healing: the next run catches up all dates.
    - Timing with recluster_topic doesn't matter: articles only appear once
      they have both a cluster_id and a sentiment_score.
    - Idempotent: running multiple times produces the same result.
    """
    clusters = session.execute(
        select(Cluster.id, Cluster.keyword).where(Cluster.topic_id == topic_id)
    ).all()

    if not clusters:
        logger.debug(f"No clusters yet for topic {topic_id}, skipping sentiment agg")
        return

    upserted_count = 0
    for cluster_id, cluster_keyword in clusters:
        # Aggregate sentiment by date for all articles in this cluster
        rows = session.execute(
            select(
                cast(Article.ingested_at, Date).label("day"),
                func.avg(Article.sentiment_score),
                func.count(Article.id),
            ).where(
                Article.cluster_id == cluster_id,
                Article.sentiment_score.isnot(None),
                Article.is_duplicate == False,
            ).group_by("day")
        ).all()

        for day, avg_sentiment, article_count in rows:
            if article_count == 0:
                continue

            existing = session.execute(
                select(SentimentHistory).where(
                    SentimentHistory.user_id == user_id,
                    SentimentHistory.cluster_id == cluster_id,
                    SentimentHistory.period_start == day,
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
                    period_start=day,
                    avg_sentiment=float(avg_sentiment),
                    article_count=article_count,
                ))
            upserted_count += 1

    logger.info(
        f"Sentiment history: upserted {upserted_count} snapshots "
        f"across {len(clusters)} clusters for topic {topic_id}"
    )
