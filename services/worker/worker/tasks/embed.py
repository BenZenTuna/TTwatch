"""Embed article text and store vector in Qdrant with user isolation payload."""
import os
import logging

import redis as redis_lib
from sqlalchemy import select
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncEmbeddingClient
from worker.tasks.utils import fetch_article_text
from app.models import Article

logger = logging.getLogger(__name__)

_embedder = SyncEmbeddingClient()
_qdrant = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)


@app.task(name="embed_article", max_retries=3, default_retry_delay=30)
@with_rls_context
def embed_article(user_id: str, article_id: str, session=None):
    """Generate embedding for an article and upsert into Qdrant.

    CRITICAL CONTRACT: Uses str(article.id) as the Qdrant point ID.
    recluster_topic depends on this: qdrant_point.id == article.id.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    # Create embedding text: title + first 1500 chars of body.
    # BGE-M3 supports up to 8192 tokens (~32K chars). Using 1500 chars
    # balances embedding quality with batch throughput.
    embed_text = f"{article.title}\n\n{raw_text[:1500]}"
    embeddings = _embedder.embed([embed_text])

    if not embeddings:
        logger.error(f"Empty embedding for article {article_id}")
        return

    # Upsert to Qdrant — point ID MUST be the article UUID
    point = PointStruct(
        id=str(article.id),
        vector=embeddings[0],
        payload={
            "user_id": user_id,
            "topic_id": str(article.topic_id),
            "title": article.title,
            "source": article.source_name or "",
            "ingested_at": article.ingested_at.isoformat() if article.ingested_at else "",
        },
    )
    _qdrant.upsert(collection_name="articles", points=[point])

    # --- Layer 3 semantic dedup: check for near-duplicate articles ---
    # After upserting, search for existing articles with cosine > 0.92.
    # Only checks within the same user + topic scope.
    try:
        similar = _qdrant.search(
            collection_name="articles",
            query_vector=embeddings[0],
            query_filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="topic_id", match=MatchValue(value=str(article.topic_id))),
            ]),
            score_threshold=0.92,
            limit=3,
        )
        for hit in similar:
            if str(hit.id) != str(article.id):
                article.is_duplicate = True
                article.duplicate_of = hit.id
                logger.info(
                    f"Semantic dedup: article {article_id} is near-duplicate "
                    f"of {hit.id} (score={hit.score:.3f})"
                )
                break
    except Exception as e:
        logger.warning(f"Semantic dedup check failed for {article_id}: {e}")

    # Store embedding reference on article
    article.embedding_id = str(article.id)
    logger.info(f"Embedded article {article_id}: {article.title[:60]}")

    # Track embedding progress and auto-dispatch clustering
    topic_id = str(article.topic_id)
    try:
        proc_prefix = f"ttwatch:processing:{topic_id}"
        current = _cache_redis.incr(f"{proc_prefix}:embedded")
        _cache_redis.expire(f"{proc_prefix}:embedded", 3600)
        expected_raw = _cache_redis.get(f"{proc_prefix}:expected")
        if expected_raw and current >= int(expected_raw) * 0.8:
            lock_key = f"{proc_prefix}:cluster_dispatched"
            if _cache_redis.set(lock_key, "1", nx=True, ex=3600):
                app.send_task("recluster_topic", args=[user_id, topic_id])
                _cache_redis.set(f"{proc_prefix}:phase", "clustering", ex=3600)
                logger.info(f"Auto-dispatched recluster_topic for topic {topic_id} ({current} embedded)")
    except Exception as e:
        logger.warning(f"Failed to update embedding progress: {e}")
