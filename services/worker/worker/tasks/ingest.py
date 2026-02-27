"""Article ingestion pipeline: fetch → extract → dedup → store → fan-out."""
import configparser
import hashlib
import logging
import os
from io import BytesIO

import redis as redis_lib
import trafilatura
from minio import Minio
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article

# Custom trafilatura config with reduced download timeout (10s vs default 30s).
# Prevents slow-failing URLs (SSL errors, redirects) from blocking workers.
_traf_config = configparser.ConfigParser()
_traf_config.read_dict({"DEFAULT": {
    "DOWNLOAD_TIMEOUT": "10",
    "MAX_REDIRECTS": "2",
    "MIN_FILE_SIZE": "0",
    "MIN_EXTRACTED_SIZE": "0",
}})

_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

# Module-level singletons — NOT created per-invocation (avoids connection
# exhaustion under gevent concurrency=32)
_minio = Minio(
    os.environ.get("MINIO_URL", "http://minio:9000")
    .replace("http://", "")
    .replace("https://", ""),
    access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
    secure=os.environ.get("MINIO_URL", "").startswith("https"),
)
_bucket = os.environ.get("MINIO_BUCKET", "ttwatch-content")

_dedup_redis = redis_lib.from_url(
    os.environ.get("REDIS_DEDUP_URL", "redis://redis:6379/2")
)


def _track_skipped_article(topic_id: str, user_id: str):
    """Decrement expected count and check if processing is effectively complete.

    Called when an article is deduplicated or fails to fetch — no fan-out tasks
    will be created, so `expected` must shrink to match. Without this, the
    clustering trigger (embedded >= expected * 0.8) never fires when most
    articles are duplicates, leaving the pipeline permanently stuck at
    "processing".
    """
    try:
        proc_prefix = f"ttwatch:processing:{topic_id}"
        new_expected = _cache_redis.decr(f"{proc_prefix}:expected")
        _cache_redis.expire(f"{proc_prefix}:expected", 7200)

        if new_expected <= 0:
            # All articles were skipped — mark complete directly
            _cache_redis.set(f"{proc_prefix}:phase", "complete", ex=7200)
            return

        # Check if embedded count now meets threshold for clustering
        embedded_raw = _cache_redis.get(f"{proc_prefix}:embedded")
        embedded = int(embedded_raw) if embedded_raw else 0
        if embedded >= new_expected * 0.8:
            lock_key = f"{proc_prefix}:cluster_dispatched"
            if _cache_redis.set(lock_key, "1", nx=True, ex=7200):
                app.send_task("recluster_topic", args=[user_id, topic_id])
                _cache_redis.set(f"{proc_prefix}:phase", "clustering", ex=7200)
                logger.info(
                    f"Auto-dispatched recluster_topic for topic {topic_id} "
                    f"(expected shrunk to {new_expected}, embedded={embedded})"
                )
    except Exception as e:
        logger.warning(f"Failed to track skipped article for topic {topic_id}: {e}")


@app.task(name="ingest_article", bind=True, max_retries=2)
@with_rls_context
def ingest_article(self, user_id: str, topic_id: str, url: str,
                   title: str = "", source_name: str = "", source_url: str = "",
                   session=None):
    """Download, extract, dedup, and store a single article.

    On success, fans out to summarize, embed, extract_entities,
    classify_sentiment, and score_relevance.

    Uses bind=True for self.retry() support. The with_rls_context decorator
    detects the Task instance and shifts arguments correctly.
    """

    # --- Layer 1: URL dedup via Redis SET ---
    dedup_key = f"ttwatch:dedup:urls:{user_id}"
    if _dedup_redis.sismember(dedup_key, url):
        logger.debug(f"URL already ingested for user {user_id}: {url}")
        try:
            ing_key = f"ttwatch:search_progress:{topic_id}:ingested"
            _cache_redis.incr(ing_key)
            _cache_redis.expire(ing_key, 7200)
        except Exception:
            pass
        _track_skipped_article(topic_id, user_id)
        return {"status": "duplicate", "layer": "url"}

    # --- Fetch and extract with trafilatura ---
    try:
        downloaded = trafilatura.fetch_url(url, config=_traf_config)
        if not downloaded:
            logger.warning(f"Failed to fetch: {url}")
            try:
                ing_key = f"ttwatch:search_progress:{topic_id}:ingested"
                _cache_redis.incr(ing_key)
                _cache_redis.expire(ing_key, 7200)
            except Exception:
                pass
            _track_skipped_article(topic_id, user_id)
            return {"status": "fetch_failed"}

        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            output_format="txt",
        )
        if not extracted or len(extracted.strip()) < 100:
            logger.warning(f"Insufficient content extracted from: {url}")
            try:
                ing_key = f"ttwatch:search_progress:{topic_id}:ingested"
                _cache_redis.incr(ing_key)
                _cache_redis.expire(ing_key, 7200)
            except Exception:
                pass
            _track_skipped_article(topic_id, user_id)
            return {"status": "extraction_failed"}
    except Exception as e:
        logger.error(f"Extraction error for {url}: {e}")
        raise self.retry(exc=e, countdown=30)

    raw_text = extracted.strip()

    # Extract title and published_at from document metadata.
    # Uses extract_metadata() which returns a structured object,
    # NOT extract(output_format="xmltei") which returns raw XML.
    published_at = None
    try:
        metadata = trafilatura.extract_metadata(downloaded)
        if metadata:
            if not title and metadata.title:
                title = metadata.title[:500]
            if metadata.date:
                from datetime import datetime as _dt
                try:
                    published_at = _dt.fromisoformat(metadata.date)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    if not title:
        title = url.split("/")[-1][:200] or "Untitled"

    # --- Layer 2: Content hash dedup ---
    content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    existing = session.execute(
        select(Article.id).where(
            Article.user_id == user_id,
            Article.topic_id == topic_id,
            Article.content_hash == content_hash,
        )
    ).scalar_one_or_none()

    if existing:
        _dedup_redis.sadd(dedup_key, url)
        logger.debug(f"Content hash duplicate: {url}")
        try:
            ing_key = f"ttwatch:search_progress:{topic_id}:ingested"
            _cache_redis.incr(ing_key)
            _cache_redis.expire(ing_key, 7200)
        except Exception:
            pass
        _track_skipped_article(topic_id, user_id)
        return {"status": "duplicate", "layer": "content_hash"}

    # --- Store raw content in MinIO ---
    storage_key = f"{user_id}/{topic_id}/{content_hash}.txt"
    raw_bytes = raw_text.encode("utf-8")
    _minio.put_object(
        _bucket, storage_key,
        BytesIO(raw_bytes), len(raw_bytes),
        content_type="text/plain",
    )

    # --- Create article record ---
    article = Article(
        user_id=user_id,
        topic_id=topic_id,
        url=url,
        title=title,
        source_name=source_name,
        source_url=source_url or None,
        published_at=published_at,
        content_hash=content_hash,
        raw_storage_key=storage_key,
    )
    session.add(article)
    session.flush()  # get article.id

    # Mark URL as ingested in Redis
    _dedup_redis.sadd(dedup_key, url)

    # --- Fan-out to ALL 5 processing tasks ---
    from worker.tasks.summarize import summarize_article
    from worker.tasks.embed import embed_article
    from worker.tasks.entities import extract_entities
    from worker.tasks.sentiment import classify_sentiment
    from worker.tasks.relevance import score_relevance

    article_id = str(article.id)
    # All sub-tasks use countdown to ensure the ingest transaction commits
    # before they try to load the article. Without this, gevent greenlets
    # can pick up embed/summarize immediately and hit NoResultFound.
    # topic_id is passed so error handlers can update progress tracking.
    embed_article.apply_async(
        args=[user_id, article_id], kwargs={"topic_id": topic_id}, countdown=1)
    summarize_article.apply_async(
        args=[user_id, article_id], kwargs={"topic_id": topic_id}, countdown=1)
    classify_sentiment.apply_async(
        args=[user_id, article_id], kwargs={"topic_id": topic_id}, countdown=3)
    score_relevance.apply_async(
        args=[user_id, article_id], kwargs={"topic_id": topic_id}, countdown=6)
    extract_entities.apply_async(
        args=[user_id, article_id], kwargs={"topic_id": topic_id}, countdown=10)

    # Track ingestion progress
    try:
        ing_key = f"ttwatch:search_progress:{topic_id}:ingested"
        _cache_redis.incr(ing_key)
        _cache_redis.expire(ing_key, 7200)
    except Exception:
        pass

    # Transition phase from "ingesting" to "processing" once fan-out begins
    try:
        proc_prefix = f"ttwatch:processing:{topic_id}"
        current_phase = _cache_redis.get(f"{proc_prefix}:phase")
        if current_phase and current_phase.decode() == "ingesting":
            _cache_redis.set(f"{proc_prefix}:phase", "processing", ex=7200)
    except Exception:
        pass

    logger.info(f"Ingested article {article_id}: {title[:80]}")
    return {"status": "ingested", "article_id": article_id}
