"""Update trend scores and velocity for clusters based on recent article activity."""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article, Cluster

logger = logging.getLogger(__name__)


@app.task(name="update_trends", queue="ttwatch:compute")
@with_rls_context
def update_trends(user_id: str, topic_id: str, session=None):
    """Compute trend scores and velocity labels for each cluster.

    trend_score = weighted sum of recent articles (newer = higher weight).
    velocity = "surging" | "rising" | "steady" | "declining" based on
               comparison between last-24h and previous-24h article counts.
    """
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    prev_24h = now - timedelta(hours=48)

    clusters = session.execute(
        select(Cluster).where(Cluster.topic_id == topic_id)
    ).scalars().all()

    for cluster in clusters:
        # Count articles in two 24-hour windows
        recent_count = session.execute(
            select(func.count(Article.id)).where(
                Article.cluster_id == cluster.id,
                Article.ingested_at >= last_24h,
                Article.is_duplicate == False,
            )
        ).scalar() or 0

        previous_count = session.execute(
            select(func.count(Article.id)).where(
                Article.cluster_id == cluster.id,
                Article.ingested_at >= prev_24h,
                Article.ingested_at < last_24h,
                Article.is_duplicate == False,
            )
        ).scalar() or 0

        # Compute velocity label
        if previous_count == 0:
            velocity = "surging" if recent_count > 3 else "rising" if recent_count > 0 else "steady"
        else:
            ratio = recent_count / previous_count
            if ratio >= 2.0:
                velocity = "surging"
            elif ratio >= 1.2:
                velocity = "rising"
            elif ratio >= 0.8:
                velocity = "steady"
            else:
                velocity = "declining"

        # Weighted trend score: 24h articles x 3 + 48h articles x 1
        cluster.trend_score = (recent_count * 3) + (previous_count * 1)
        cluster.velocity = velocity

    logger.info(f"Updated trends for {len(clusters)} clusters in topic {topic_id}")
