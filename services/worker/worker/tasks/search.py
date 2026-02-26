"""Search task — query SearXNG and dispatch article ingestion."""
import logging
import os

import httpx
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Topic

logger = logging.getLogger(__name__)

_searxng_url = os.environ.get("SEARXNG_URL", "http://searxng:8080")
_http = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))


@app.task(name="run_topic_search")
@with_rls_context
def run_topic_search(user_id: str, topic_id: str, session=None):
    """Fetch topic config, build search queries, call SearXNG, dispatch ingestion.

    For each topic, builds queries from:
    1. The topic name itself
    2. Any configured search_terms in topic.config

    Deduplicates URLs within the batch before dispatching ingest_article.
    """
    topic = session.execute(
        select(Topic).where(Topic.id == topic_id)
    ).scalar_one_or_none()

    if not topic:
        logger.warning(f"Topic {topic_id} not found for user {user_id}")
        return {"status": "topic_not_found"}

    # Build query list from topic name + configured search terms
    queries = [topic.name]
    config = topic.config or {}
    search_terms = config.get("search_terms", [])
    if isinstance(search_terms, list):
        for term in search_terms:
            if isinstance(term, str) and term.strip():
                queries.append(term.strip())

    # Collect all results, deduplicate by URL within this batch
    seen_urls = set()
    results = []

    for query in queries:
        try:
            resp = _http.get(
                f"{_searxng_url}/search",
                params={"q": query, "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"SearXNG search failed for query '{query}': {e}")
            continue

        for item in data.get("results", []):
            url = item.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({
                "url": url,
                "title": item.get("title", ""),
                "source_name": item.get("engine", ""),
                "source_url": item.get("parsed_url", [""])[0] if item.get("parsed_url") else "",
            })

    # Dispatch ingestion for each unique result
    for r in results:
        app.send_task("ingest_article", args=[
            user_id,
            topic_id,
            r["url"],
        ], kwargs={
            "title": r["title"],
            "source_name": r["source_name"],
            "source_url": r["source_url"],
        })

    logger.info(
        f"run_topic_search: dispatched {len(results)} articles "
        f"for topic '{topic.name}' ({len(queries)} queries)"
    )
    return {"status": "ok", "dispatched": len(results)}
