"""Article ingestion pipeline: fetch → extract → dedup → store → fan-out."""
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


@app.task(name="ingest_article", bind=True, max_retries=2)
@with_rls_context
def ingest_article(self, user_id: str, topic_id: str, url: str,
                   title: str = "", source_name: str = "", source_url: str = "",
                   session=None):
    """Download, extract, dedup, and store a single article.

    On success, fans out to summarize, embed, extract_entities, classify_sentiment.

    Uses bind=True for self.retry() support. The with_rls_context decorator
    detects the Task instance and shifts arguments correctly.
    """

    # --- Layer 1: URL dedup via Redis SET ---
    dedup_key = f"ttwatch:dedup:urls:{user_id}"
    if _dedup_redis.sismember(dedup_key, url):
        logger.debug(f"URL already ingested for user {user_id}: {url}")
        return {"status": "duplicate", "layer": "url"}

    # --- Fetch and extract with trafilatura ---
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning(f"Failed to fetch: {url}")
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

    # --- Fan-out to ALL 4 processing tasks ---
    from worker.tasks.summarize import summarize_article
    from worker.tasks.embed import embed_article
    from worker.tasks.entities import extract_entities
    from worker.tasks.sentiment import classify_sentiment

    article_id = str(article.id)
    summarize_article.delay(user_id, article_id)
    embed_article.delay(user_id, article_id)
    extract_entities.delay(user_id, article_id)
    classify_sentiment.delay(user_id, article_id)

    logger.info(f"Ingested article {article_id}: {title[:80]}")
    return {"status": "ingested", "article_id": article_id}
