"""Generate optimized search queries from natural-language topic names using LLM."""
import json
import logging
import os
from datetime import datetime, timezone

import redis as redis_lib
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_router import get_llm_for_task
from app.models import Topic

_search_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

TASK_CATEGORY = "search_planning"

_SYSTEM_PROMPT = (
    "You are a search query optimizer for a news intelligence platform. Given a topic description, "
    "generate diverse search engine queries that provide BROAD coverage of the entire topic "
    "ecosystem — not just the primary subject.\n\n"
    "STEP 1 — EXTRACT ENTITIES from the topic description:\n\n"
    "Primary subject: the main thing being tracked\n"
    "Named alternatives: any explicitly mentioned products, tools, or competitors\n"
    "Implicit ecosystem: the broader category implied by the topic\n\n"
    "STEP 2 — GENERATE QUERIES across these mandatory categories:\n"
    "A. PRIMARY NEWS (1-2 queries): latest news or releases for the primary subject\n"
    "B. NAMED ALTERNATIVES (1 query per named product/tool mentioned): target each explicitly "
    "named alternative with its own dedicated query\n"
    "C. ECOSYSTEM-WIDE (1-2 queries): broader category queries that do NOT contain the primary "
    "subject name\n"
    "D. CHANGELOG/RELEASES (1-2 queries): use terms like \"release\", \"changelog\", \"new version\", "
    "\"update\", \"roadmap\" to surface version news\n"
    "E. COMPARISON/ALTERNATIVES (1 query): explicitly seek alternatives, comparisons, or competing "
    "approaches\n\n"
    "RULES:\n\n"
    "Each query must be 2-8 words\n"
    "No two queries should target the same named entity or product\n"
    "At least 2 queries must NOT contain the primary subject's name (forces ecosystem coverage)\n"
    "Include the current year in at least 2 queries for freshness\n"
    "For category D, prefer specific version/release language over generic news language\n"
    "If the topic mentions 3 or more named products, generate at least one query per named product\n\n"
    "Return ONLY valid JSON with no explanation, preamble, or markdown:\n"
    '{"queries": ["query1", "query2", ...], "primary_entity": "...", '
    '"named_alternatives": ["...", "..."], "categories_covered": ["A", "B", "C", "D", "E"]}'
)


@app.task(name="generate_search_queries", max_retries=3, default_retry_delay=30)
@with_rls_context
def generate_search_queries(user_id: str, topic_id: str, session=None):
    """Decompose a natural-language topic name into 3-6 focused SearXNG search queries.

    Uses the LLM (Tier 1, ≤4K tokens) to extract core intent and key entities
    from the topic name, then generates short, specific search queries optimized
    for SearXNG. Stores the queries in topic.config["search_queries"] and
    dispatches run_topic_search to execute them.
    """
    topic = session.execute(
        select(Topic).where(Topic.id == topic_id)
    ).scalar_one_or_none()

    if not topic:
        logger.warning(f"Topic {topic_id} not found for user {user_id}")
        return {"status": "topic_not_found"}

    # Set status to generating_queries
    now = datetime.now(timezone.utc).isoformat()
    try:
        status_key = f"ttwatch:search_status:{topic_id}"
        _search_redis.setex(status_key, 3600, json.dumps({
            "status": "generating_queries",
            "started_at": now,
            "user_id": user_id,
        }))
        _search_redis.setex(
            f"ttwatch:search_progress:{topic_id}:started_at", 7200, now
        )
    except Exception as e:
        logger.warning(f"Failed to set generating_queries status: {e}")

    # Scale query count based on topic complexity
    topic_words = len(topic.name.split())
    named_product_count = sum(
        1 for word in topic.name.split()
        if word[0].isupper() and len(word) > 3
    ) if topic.name else 0

    if named_product_count >= 3 or topic_words > 20:
        target_queries = 10
    elif named_product_count >= 2 or topic_words > 10:
        target_queries = 8
    else:
        target_queries = 6

    user_content = (
        f"Topic: {topic.name}\n\n"
        f"Generate exactly {target_queries} queries following all mandatory categories above. "
        f"If this topic mentions multiple named products or alternatives, ensure each gets its own dedicated query."
    )

    llm = get_llm_for_task(session, user_id, TASK_CATEGORY)
    result = llm.generate_json([
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ], max_tokens=512)

    queries = result.get("queries", [])
    primary_entity = result.get("primary_entity", "unknown")
    named_alternatives = result.get("named_alternatives", [])
    categories_covered = result.get("categories_covered", [])

    logger.info(
        f"[search_plan] topic={topic.id} primary='{primary_entity}' "
        f"alternatives={named_alternatives} categories={categories_covered} "
        f"query_count={len(queries)}"
    )

    # Validate: must be a list of non-empty strings, 3-6 items
    queries = [
        q.strip() for q in queries
        if isinstance(q, str) and q.strip()
    ][:10]

    if len(queries) < 2:
        # LLM gave too few queries — fall back to splitting the topic name
        # into meaningful phrases rather than using it raw
        logger.warning(
            f"LLM returned {len(queries)} queries for topic '{topic.name}', "
            "using topic name as single query fallback"
        )
        queries = [topic.name]

    # Store generated queries in topic config
    config = dict(topic.config) if topic.config else {}
    config["search_queries"] = queries
    topic.config = config

    logger.info(
        f"Generated {len(queries)} search queries for topic '{topic.name}': {queries}"
    )

    # Commit so run_topic_search can see the updated config.
    # Without this, the session commits after this function returns,
    # but the dispatched task runs immediately in a new session.
    session.commit()

    # Dispatch the actual SearXNG searches using the generated queries
    app.send_task("run_topic_search", args=[user_id, topic_id])

    return {"status": "ok", "queries": queries}
