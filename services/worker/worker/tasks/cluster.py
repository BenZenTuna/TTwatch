"""Re-cluster articles for a topic using HDBSCAN on Qdrant embeddings."""
import json
import os
import re
import logging
from datetime import datetime, timezone

import numpy as np
import redis as redis_lib
from umap import UMAP
from hdbscan import HDBSCAN
from sqlalchemy import select, delete
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Cluster, Article


_STOPWORDS = frozenset(
    "a an the and or but in on at to for of is are was were be been being "
    "by with from as it its this that these those has have had do does did "
    "will would shall should can could may might not no so if how what when "
    "where who which whom why all each every both few many much some any "
    "here there about after before between into through during above below "
    "up down out off over under again further then once re vs via new s t "
    "your our my his her their its we you they i he she me us".split()
)


def _extract_label_from_titles(titles: list[str]) -> str:
    """Extract a 2-4 word cluster label from article titles using word frequency.

    Fast, deterministic alternative to LLM-based labeling. Avoids the
    extreme latency of reasoning models (QwQ) on simple classification tasks.
    """
    from collections import Counter

    words: list[str] = []
    for title in titles:
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]*[A-Za-z0-9]|[A-Za-z]", title):
            lower = word.lower()
            if lower not in _STOPWORDS and len(word) > 1:
                words.append(word)

    if not words:
        return "Uncategorized"

    # Count frequencies, preferring capitalized words (proper nouns / brand names)
    freq = Counter(words)
    # Boost proper nouns (capitalized words that aren't start-of-title)
    proper_nouns = Counter()
    for title in titles:
        title_words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*[A-Za-z0-9]|[A-Za-z]", title)
        for j, w in enumerate(title_words):
            if j > 0 and w[0].isupper() and w.lower() not in _STOPWORDS:
                proper_nouns[w] += 1

    # Merge: proper nouns get 2x weight
    combined: Counter = Counter()
    for word, count in freq.items():
        combined[word] = count + proper_nouns.get(word, 0)

    # Pick top 2-3 distinct words (case-preserving, deduplicated)
    seen_lower: set[str] = set()
    label_parts: list[str] = []
    for word, _ in combined.most_common(20):
        if word.lower() not in seen_lower:
            seen_lower.add(word.lower())
            label_parts.append(word)
            if len(label_parts) >= 3:
                break

    return " ".join(label_parts) if label_parts else "Uncategorized"

_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

qdrant_sync = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))

CLUSTER_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1",
    "#14B8A6", "#E11D48", "#A855F7", "#0EA5E9", "#D946EF",
]


@app.task(name="recluster_topic", queue="ttwatch:compute")
@with_rls_context
def recluster_topic(user_id: str, topic_id: str, session=None):
    """Re-cluster articles for a topic using HDBSCAN.

    Two-phase Qdrant scroll:
      Phase 1: Scroll ALL points (no vectors) for sorting by ingested_at.
      Phase 2: Retrieve selected points WITH vectors for clustering.

    CRITICAL: Before deleting old clusters, nullify sentiment_history.cluster_id
    and delete entity_cluster_map entries. This preserves historical data that
    would otherwise be lost to ON DELETE CASCADE / SET NULL every 2 hours.
    """
    MAX_CLUSTER_ARTICLES = 2000
    scroll_filter = Filter(must=[
        FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        FieldCondition(key="topic_id", match=MatchValue(value=topic_id)),
    ])

    # Phase 1: Scroll payloads only (no vectors) for sorting
    all_points = []
    offset = None
    while True:
        points, next_offset = qdrant_sync.scroll(
            collection_name="articles",
            scroll_filter=scroll_filter,
            offset=offset,
            limit=500,
            with_vectors=False,
            with_payload=True,
        )
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset

    all_points.sort(key=lambda p: p.payload.get("ingested_at", ""), reverse=True)
    candidate_ids = [p.id for p in all_points[:MAX_CLUSTER_ARTICLES]]

    # Exclude low-relevance and duplicate articles from clustering.
    # relevance_score lives in PostgreSQL, not Qdrant payloads, so we
    # filter here after the Qdrant scroll.
    if candidate_ids:
        from worker.tasks.relevance import RELEVANCE_THRESHOLD
        excluded = set(
            str(row[0]) for row in session.execute(
                select(Article.id).where(
                    Article.id.in_([str(cid) for cid in candidate_ids]),
                    Article.is_duplicate == True,
                )
            ).all()
        )
        low_relevance = set(
            str(row[0]) for row in session.execute(
                select(Article.id).where(
                    Article.id.in_([str(cid) for cid in candidate_ids]),
                    Article.relevance_score.isnot(None),
                    Article.relevance_score < RELEVANCE_THRESHOLD,
                )
            ).all()
        )
        excluded |= low_relevance
        selected_ids = [cid for cid in candidate_ids if str(cid) not in excluded]
        if excluded:
            logger.info(
                f"Topic {topic_id}: excluded {len(excluded)} articles "
                f"(low-relevance/duplicate) from clustering"
            )
    else:
        selected_ids = candidate_ids

    if len(selected_ids) < 10:
        logger.info(f"Topic {topic_id}: only {len(selected_ids)} articles, skipping")
        # Mark pipeline complete so search_status doesn't stay at "processing"
        try:
            proc_prefix = f"ttwatch:processing:{topic_id}"
            _cache_redis.set(f"{proc_prefix}:phase", "complete", ex=7200)
            status_key = f"ttwatch:search_status:{topic_id}"
            raw = _cache_redis.get(status_key)
            if raw:
                data = json.loads(raw)
                if data.get("status") == "processing":
                    now = datetime.now(timezone.utc).isoformat()
                    completed_payload = {
                        "status": "completed",
                        "completed_at": now,
                        "started_at": data.get("started_at", now),
                        "articles_found": data.get("articles_found", 0),
                        "cluster_count": 0,
                        "user_id": data.get("user_id", user_id),
                    }
                    _cache_redis.setex(status_key, 3600, json.dumps(completed_payload))
                    _cache_redis.publish("ttwatch:search:completed", json.dumps({
                        **completed_payload,
                        "type": "search_completed",
                        "topic_id": topic_id,
                    }))
        except Exception as e:
            logger.warning(f"Failed to finalize status for topic {topic_id}: {e}")
        return

    # Phase 2: Fetch vectors only for selected points
    points_with_vectors = qdrant_sync.retrieve(
        collection_name="articles",
        ids=selected_ids,
        with_vectors=True,
        with_payload=True,
    )

    vectors = np.array([p.vector for p in points_with_vectors])
    reduced = UMAP(n_components=20, metric="cosine", random_state=42).fit_transform(vectors)
    labels = HDBSCAN(min_cluster_size=5, min_samples=3).fit_predict(reduced)

    # Clear old clusters for this topic.
    # IMPORTANT: Nullify FK references in sentiment_history and entity_cluster_map
    # BEFORE deleting clusters. Without this, ON DELETE CASCADE would permanently
    # destroy historical sentiment data and entity-cluster mappings every 2 hours.
    old_cluster_ids = [
        row[0] for row in session.execute(
            select(Cluster.id).where(Cluster.topic_id == topic_id)
        ).all()
    ]
    if old_cluster_ids:
        from app.models import SentimentHistory, EntityClusterMap
        session.execute(
            SentimentHistory.__table__.update()
            .where(SentimentHistory.cluster_id.in_(old_cluster_ids))
            .values(cluster_id=None)
        )
        session.execute(
            EntityClusterMap.__table__.delete()
            .where(EntityClusterMap.cluster_id.in_(old_cluster_ids))
        )
        session.execute(delete(Cluster).where(Cluster.topic_id == topic_id))
        session.flush()

    unique_labels = sorted(set(labels) - {-1})
    for i, cluster_label in enumerate(unique_labels):
        cluster_point_indices = [idx for idx, l in enumerate(labels) if l == cluster_label]
        cluster_articles = [points_with_vectors[idx] for idx in cluster_point_indices]

        title_list = [p.payload.get("title", "Untitled") for p in cluster_articles[:10]]
        keyword = _extract_label_from_titles(title_list)

        article_count = len(cluster_articles)
        cluster = Cluster(
            user_id=user_id, topic_id=topic_id, keyword=keyword,
            color=CLUSTER_COLORS[i % len(CLUSTER_COLORS)],
            article_count=article_count, trend_score=article_count,
        )
        session.add(cluster)
        session.flush()

        article_ids = [str(p.id) for p in cluster_articles]
        # CRITICAL CONTRACT: article_ids here are Qdrant point IDs, which
        # MUST equal PostgreSQL article UUIDs. This is enforced by embed_article
        # (worker/tasks/embed.py) which uses str(article.id) as the point ID.
        #
        # NOTE: Some Qdrant points may be orphaned (article deleted from PG
        # but vector remains in Qdrant). The UPDATE below correctly updates
        # only existing articles. After the update, recalculate the actual
        # article count from the database to avoid inflation from orphans.
        result = session.execute(
            Article.__table__.update().where(Article.id.in_(article_ids)).values(cluster_id=cluster.id)
        )
        # Update article_count to reflect actual DB rows, not Qdrant point count
        actual_count = result.rowcount if hasattr(result, 'rowcount') else article_count
        if actual_count != article_count:
            cluster.article_count = actual_count
            cluster.trend_score = actual_count

    noise_ids = [str(points_with_vectors[idx].id) for idx, l in enumerate(labels) if l == -1]
    if noise_ids:
        session.execute(
            Article.__table__.update().where(Article.id.in_(noise_ids)).values(cluster_id=None)
        )

    logger.info(f"Topic {topic_id}: {len(unique_labels)} clusters from {len(selected_ids)} articles ({len(noise_ids)} noise)")

    # Mark processing as complete
    try:
        proc_prefix = f"ttwatch:processing:{topic_id}"
        _cache_redis.set(f"{proc_prefix}:phase", "complete", ex=7200)
        _cache_redis.set(f"{proc_prefix}:cluster_count", len(unique_labels), ex=7200)

        # Update search status to completed
        now = datetime.now(timezone.utc).isoformat()
        started_at_raw = _cache_redis.get(f"ttwatch:search_progress:{topic_id}:started_at")
        started_at = started_at_raw.decode() if started_at_raw else now

        completed_payload = {
            "status": "completed",
            "completed_at": now,
            "started_at": started_at,
            "cluster_count": len(unique_labels),
            "user_id": user_id,
        }
        status_key = f"ttwatch:search_status:{topic_id}"
        _cache_redis.setex(status_key, 3600, json.dumps(completed_payload))
        _cache_redis.publish("ttwatch:search:completed", json.dumps({
            **completed_payload,
            "type": "search_completed",
            "topic_id": topic_id,
        }))
    except Exception:
        pass
