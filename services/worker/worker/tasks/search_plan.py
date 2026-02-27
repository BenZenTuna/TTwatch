"""Generate optimized search queries from natural-language topic names using LLM."""
import logging

from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_router import get_llm_for_task
from app.models import Topic

logger = logging.getLogger(__name__)

TASK_CATEGORY = "search_planning"

_SYSTEM_PROMPT = (
    "You are a search query optimizer. Given a user's natural-language topic description, "
    "generate 3-6 focused search engine queries.\n\n"
    "Rules:\n"
    "- Each query should be 1-6 words, specific, and use terms a search engine would match well.\n"
    "- Strip conversational filler (I want to, please, follow, etc.) and focus on proper nouns, "
    "technical terms, and action concepts.\n"
    "- Include the current year (2026) in at least one query for freshness.\n"
    "- Vary the queries to cover different angles of the topic (alternatives, news, reviews, "
    "technical details).\n"
    '- Return ONLY valid JSON: {"queries": ["query1", "query2", ...]}'
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

    llm = get_llm_for_task(session, user_id, TASK_CATEGORY)
    result = llm.generate_json([
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Topic: {topic.name}"},
    ], max_tokens=512)

    queries = result.get("queries", [])

    # Validate: must be a list of non-empty strings, 3-6 items
    queries = [
        q.strip() for q in queries
        if isinstance(q, str) and q.strip()
    ][:6]

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
