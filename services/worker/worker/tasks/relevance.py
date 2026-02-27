"""Score article relevance against its parent topic using LLM."""
import logging
import os
import re

import redis as redis_lib
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import create_fast_client
from worker.tasks.utils import fetch_article_text
from app.models import Article, Topic

_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

_llm = create_fast_client()

RELEVANCE_THRESHOLD = 0.3


@app.task(name="score_relevance", max_retries=2, default_retry_delay=60)
@with_rls_context
def score_relevance(user_id: str, article_id: str, session=None):
    """Score how relevant an article is to its parent topic.

    Uses the LLM to rate relevance on a 0.0–1.0 scale. Articles scoring
    below RELEVANCE_THRESHOLD are marked is_duplicate=True so they are
    filtered from display, clustering, and downstream processing.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    # Skip articles already flagged as duplicates by semantic dedup
    if article.is_duplicate and article.duplicate_of is not None:
        logger.debug(f"Skipping relevance scoring for duplicate article {article_id}")
        return

    topic = session.execute(
        select(Topic).where(Topic.id == article.topic_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    response = _llm.generate([
        {"role": "system", "content": (
            "Rate how relevant this article is to the given monitoring topic. "
            "Respond with ONLY a single decimal number from 0.0 to 1.0. "
            "0.0 = completely irrelevant, 1.0 = highly relevant. "
            "Do NOT include any explanation."
        )},
        {"role": "user", "content": (
            f"Monitoring topic: {topic.name}\n"
            f"Article title: {article.title}\n"
            f"Article excerpt: {raw_text[:500]}"
        )},
    ], max_tokens=10, temperature=0.1)

    # Parse the float from the response
    match = re.search(r"([01]\.?\d*)", response.strip())
    if match:
        score = float(match.group(1))
    else:
        # If parsing fails, assume marginally relevant rather than filtering
        logger.warning(
            f"Could not parse relevance score from LLM response "
            f"for article {article_id}: {response[:50]!r}"
        )
        score = 0.5

    score = max(0.0, min(1.0, score))
    article.relevance_score = score

    if score < RELEVANCE_THRESHOLD:
        article.is_duplicate = True
        logger.info(
            f"Low relevance filtered: article {article_id} "
            f"scored {score:.2f} against topic '{topic.name}': {article.title[:60]}"
        )
    else:
        logger.info(f"Relevance for article {article_id}: {score:.2f}")

    # Track relevance progress
    try:
        key = f"ttwatch:processing:{article.topic_id}:relevance"
        _cache_redis.incr(key)
        _cache_redis.expire(key, 3600)
    except Exception:
        pass
