"""Detect coverage gaps by analyzing what the current clusters DON'T cover."""
import logging

from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_router import get_llm_for_task
from app.models import Cluster, Briefing, Topic

logger = logging.getLogger(__name__)

TASK_CATEGORY = "coverage_gaps"


@app.task(name="detect_coverage_gaps", max_retries=2, default_retry_delay=60)
@with_rls_context
def detect_coverage_gaps(user_id: str, topic_id: str, session=None):
    """Use LLM to identify topics NOT covered by existing clusters.

    Compares cluster keywords against what a comprehensive analysis of the
    topic domain should include. Stores gaps in the latest briefing record.
    """
    clusters = session.execute(
        select(Cluster.keyword, Cluster.article_count).where(
            Cluster.topic_id == topic_id
        ).order_by(Cluster.article_count.desc())
    ).all()

    if not clusters:
        return

    covered_topics = "\n".join(f"- {kw} ({count} articles)" for kw, count in clusters)

    # Get the topic name for context
    topic = session.execute(
        select(Topic.name).where(Topic.id == topic_id)
    ).scalar_one_or_none()
    topic_name = topic or "this topic"

    llm = get_llm_for_task(session, user_id, TASK_CATEGORY)
    result = llm.generate_json([
        {"role": "system", "content": (
            "You are an intelligence analyst. Given the currently covered subtopics, "
            "identify 3-5 important areas that are NOT being covered but SHOULD be "
            "for comprehensive monitoring. Return JSON: "
            "{\"gaps\": [{\"area\": \"...\", \"reason\": \"why this matters\"}]}"
        )},
        {"role": "user", "content": (
            f"Topic: {topic_name}\n\n"
            f"Currently covered subtopics:\n{covered_topics}\n\n"
            "What important areas are missing from this coverage?"
        )},
    ])

    gaps = result.get("gaps", [])

    # Update the latest briefing with coverage gaps
    latest_briefing = session.execute(
        select(Briefing).where(
            Briefing.topic_id == topic_id
        ).order_by(Briefing.generated_at.desc()).limit(1)
    ).scalar_one_or_none()

    if latest_briefing:
        latest_briefing.coverage_gaps = gaps

    logger.info(f"Detected {len(gaps)} coverage gaps for topic {topic_id}")
