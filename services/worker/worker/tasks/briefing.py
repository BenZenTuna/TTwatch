"""Generate topic briefing from cluster summaries using hierarchical summarization."""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import Article, Cluster, Briefing, Entity

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="generate_briefing", max_retries=2, default_retry_delay=60)
@with_rls_context
def generate_briefing(user_id: str, topic_id: str, session=None):
    """Generate an intelligence briefing for a topic using hierarchical summarization.

    Tier 3 task (~16K context): Aggregates cluster summaries into a single briefing.
    Uses the hierarchy: articles -> article summaries -> cluster summaries -> briefing.
    This avoids sending raw article text directly to the LLM.
    """
    clusters = session.execute(
        select(Cluster).where(
            Cluster.topic_id == topic_id
        ).order_by(Cluster.article_count.desc())
    ).scalars().all()

    if not clusters:
        logger.info(f"No clusters for topic {topic_id}, skipping briefing")
        return

    # Build cluster summaries from article summaries
    cluster_sections = []
    total_articles = 0
    for cluster in clusters[:12]:  # Cap at 12 clusters to stay in context window
        articles = session.execute(
            select(Article).where(
                Article.cluster_id == cluster.id,
                Article.summary.isnot(None),
                Article.is_duplicate == False,
            ).order_by(Article.ingested_at.desc()).limit(20)
        ).scalars().all()

        if not articles:
            continue

        summaries = "\n".join(
            f"- [{a.source_name or 'Unknown'}] {a.summary}" for a in articles
        )
        cluster_sections.append(
            f"### {cluster.keyword} ({cluster.article_count} articles)\n{summaries}"
        )
        total_articles += len(articles)

    if not cluster_sections:
        logger.info(f"No summarized articles for topic {topic_id}, skipping briefing")
        return

    cluster_text = "\n\n".join(cluster_sections)

    # Detect new entities (first seen in last 24h)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    new_entities = session.execute(
        select(Entity.name, Entity.type).where(
            Entity.topic_id == topic_id,
            Entity.first_seen >= cutoff,
        ).limit(20)
    ).all()
    new_entity_list = [{"name": e[0], "type": e[1]} for e in new_entities]

    result = _llm.generate_json([
        {"role": "system", "content": (
            "You are an intelligence analyst. Generate a briefing from these cluster summaries. "
            "Return JSON: {\"summary\": \"2-3 paragraph executive summary\", "
            "\"highlights\": [\"key development 1\", ...], "
            "\"watch_items\": [\"thing to monitor\", ...]}"
        )},
        {"role": "user", "content": f"Topic clusters:\n\n{cluster_text}"},
    ], max_tokens=2048)

    briefing = Briefing(
        user_id=user_id,
        topic_id=topic_id,
        summary=result.get("summary", ""),
        highlights=result.get("highlights", []),
        new_entities=new_entity_list,
        watch_items=result.get("watch_items", []),
        model_used=_llm.model,
    )
    session.add(briefing)

    logger.info(f"Generated briefing for topic {topic_id}: {total_articles} articles across {len(cluster_sections)} clusters")
