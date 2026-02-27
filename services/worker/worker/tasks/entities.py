"""Extract named entities from article text using LLM."""
import logging
import os

import redis as redis_lib
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_router import get_llm_for_task
from worker.tasks.utils import fetch_article_text
from app.models import Article, Entity, EntityArticleMap

_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

TASK_CATEGORY = "entity_extraction"


@app.task(name="extract_entities", max_retries=3, default_retry_delay=30)
@with_rls_context
def extract_entities(user_id: str, article_id: str, session=None):
    """Extract named entities from an article and persist to database.

    Creates Entity records if they don't exist, and creates
    EntityArticleMap join records linking entities to the article.
    For newly created org/product/technology entities, fans out to
    resolve_entity_ticker.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    llm = get_llm_for_task(session, user_id, TASK_CATEGORY)
    result = llm.generate_json([
        {"role": "system", "content": (
            "Extract named entities from the article. Return JSON: "
            '{"entities": [{"name": "...", "type": "person|org|product|location|event|technology"}]}. '
            "Only include clearly identified entities. Max 15. "
            "Respond with only the requested format. Do not include explanations."
        )},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ])

    entities = result.get("entities", [])
    for ent in entities:
        name = ent.get("name", "").strip()[:500]
        etype = ent.get("type", "unknown").strip().lower()[:50]
        if not name:
            continue

        # Upsert entity (unique per user + name + type + topic)
        existing = session.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.name == name,
                Entity.type == etype,
                Entity.topic_id == str(article.topic_id),
            )
        ).scalar_one_or_none()

        if existing:
            entity_id = existing.id
        else:
            entity = Entity(
                user_id=user_id,
                topic_id=str(article.topic_id),
                name=name,
                type=etype,
            )
            session.add(entity)
            session.flush()
            entity_id = entity.id

        # Link entity to article (skip if already linked)
        exists_link = session.execute(
            select(EntityArticleMap).where(
                EntityArticleMap.entity_id == entity_id,
                EntityArticleMap.article_id == article_id,
            )
        ).scalar_one_or_none()
        if not exists_link:
            session.add(EntityArticleMap(
                entity_id=entity_id,
                article_id=article_id,
                user_id=user_id,
            ))

        # Fan-out: resolve newly created entities to ticker symbols
        if not existing and etype in ("org", "product", "technology"):
            from worker.tasks.resolve_ticker import resolve_entity_ticker
            resolve_entity_ticker.delay(user_id, str(entity_id), str(article.topic_id))

    logger.info(f"Extracted {len(entities)} entities from article {article_id}")

    # Track entity extraction progress
    try:
        key = f"ttwatch:processing:{article.topic_id}:entities"
        _cache_redis.incr(key)
        _cache_redis.expire(key, 7200)
        agg_key = f"ttwatch:search_progress:{article.topic_id}:tasks_completed"
        _cache_redis.incr(agg_key)
        _cache_redis.expire(agg_key, 7200)
    except Exception:
        pass
