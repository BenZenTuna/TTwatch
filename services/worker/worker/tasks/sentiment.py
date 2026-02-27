"""Classify article sentiment using LLM."""
import logging
import os

import redis as redis_lib
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from worker.tasks.utils import fetch_article_text
from app.models import Article

_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="classify_sentiment", max_retries=3, default_retry_delay=30)
@with_rls_context
def classify_sentiment(user_id: str, article_id: str, session=None):
    """Classify sentiment of an article on a -1.0 to 1.0 scale.

    -1.0 = strongly negative, 0.0 = neutral, 1.0 = strongly positive.
    Stores the result on the article's sentiment_score column.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    result = _llm.generate_json([
        {"role": "system", "content": (
            "Classify the sentiment of this article on a scale from -1.0 to 1.0. "
            "-1.0 = strongly negative, 0.0 = neutral, 1.0 = strongly positive. "
            'Return JSON: {"score": 0.0, "rationale": "brief explanation"}'
        )},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ])

    score = result.get("score", 0.0)
    score = max(-1.0, min(1.0, float(score)))
    article.sentiment_score = score

    logger.info(f"Sentiment for article {article_id}: {score:.2f}")

    # Track sentiment progress
    try:
        key = f"ttwatch:processing:{article.topic_id}:sentiment"
        _cache_redis.incr(key)
        _cache_redis.expire(key, 3600)
    except Exception:
        pass
