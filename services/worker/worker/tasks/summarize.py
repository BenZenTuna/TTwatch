"""Summarize article text using LLM."""
import logging
import os
import re

import redis as redis_lib
from sqlalchemy import select, or_
from sqlalchemy.exc import NoResultFound

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.db import db_session
from worker.llm_router import get_llm_for_task
from worker.tasks.utils import fetch_article_text
from app.models import Article

_cache_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

logger = logging.getLogger(__name__)

TASK_CATEGORY = "summarization"

_SYSTEM_PROMPT = (
    "Summarize this article in exactly two sentences. "
    "Respond with ONLY the summary. "
    "Do not include explanations, reasoning, or meta-commentary."
)

# Patterns that indicate chain-of-thought leakage (kept for reprocess_summaries)
_COT_PREFIXES = re.compile(
    r"^(?:Okay|OK|Alright|Sure|So|First|Well|Let me|I need to|I'll|Hmm|"
    r"Let's|The user|I should|I will|Looking at|Reading|After reading)[,.]?\s",
    re.IGNORECASE,
)

_COT_PHRASES = re.compile(
    r"(?:let me (?:read|summarize|think|look)|"
    r"i need to (?:summarize|read|find)|"
    r"the user (?:wants|asked|is asking)|"
    r"i (?:should|will|need to) (?:provide|write|create)|"
    r"here(?:'s| is) (?:the|my|a) summary)",
    re.IGNORECASE,
)


def clean_summary(text: str) -> str:
    """Strip chain-of-thought leakage from LLM summary output.

    Handles common patterns where the model emits reasoning tokens before
    the actual summary content. Kept for reprocess_summaries migration task.
    """
    cleaned = text.strip()
    if not cleaned:
        return cleaned

    # If the response contains CoT phrases, try to find where the actual summary starts
    if _COT_PHRASES.search(cleaned):
        sentences = re.split(r'(?<=[.!?])\s+', cleaned)

        content_sentences = []
        for s in sentences:
            if _COT_PREFIXES.match(s) or _COT_PHRASES.search(s):
                continue
            content_sentences.append(s)

        if content_sentences:
            candidate = " ".join(content_sentences[-3:])
            if len(candidate) >= 20:
                cleaned = candidate

    for _ in range(3):
        match = _COT_PREFIXES.match(cleaned)
        if match:
            cleaned = cleaned[match.end():].strip()
        else:
            break

    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()

    if len(cleaned) < 20:
        cleaned = text.strip()

    return cleaned


@app.task(name="summarize_article", max_retries=3, default_retry_delay=30)
@with_rls_context
def summarize_article(user_id: str, article_id: str, topic_id: str = None,
                      session=None):
    """Generate a 2-sentence summary (100-150 tokens) and store on article."""
    try:
        article = session.execute(
            select(Article).where(Article.id == article_id)
        ).scalar_one()
    except NoResultFound:
        logger.warning(f"Article {article_id} not found (deleted by dedup?), skipping summarize")
        return

    raw_text = fetch_article_text(article.raw_storage_key)

    llm = get_llm_for_task(session, user_id, TASK_CATEGORY)
    summary = llm.generate([
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ], max_tokens=150)

    article.summary = clean_summary(summary)

    logger.info(f"Summarized article {article_id}: {article.title[:60]}")

    # Track summarization progress
    try:
        key = f"ttwatch:processing:{article.topic_id}:summarized"
        _cache_redis.incr(key)
        _cache_redis.expire(key, 7200)
        agg_key = f"ttwatch:search_progress:{article.topic_id}:tasks_completed"
        _cache_redis.incr(agg_key)
        _cache_redis.expire(agg_key, 7200)
    except Exception:
        pass


@app.task(name="reprocess_summaries")
def reprocess_summaries():
    """One-time migration task: clean CoT leakage from existing article summaries.

    Finds all articles whose summaries start with common chain-of-thought
    prefixes and applies clean_summary() to fix them in place. Articles
    where cleaning doesn't improve the text are re-summarized via LLM.

    Run manually: celery -A worker.celeryconfig call reprocess_summaries
    """
    cot_patterns = ["Okay%", "OK,%", "Let me%", "I need to%", "So,%",
                    "First,%", "Alright%", "Sure%", "Here is%", "Here's%",
                    "Well,%", "Looking at%", "Hmm%"]

    with db_session() as session:
        conditions = [Article.summary.like(pat) for pat in cot_patterns]
        dirty_articles = session.execute(
            select(Article.id, Article.user_id, Article.summary)
            .where(Article.summary.isnot(None))
            .where(or_(*conditions))
        ).all()

        cleaned_count = 0
        resend_count = 0

        for article_id, user_id, summary in dirty_articles:
            cleaned = clean_summary(summary)

            # If cleaning produced a meaningfully different result, update in place
            if cleaned != summary.strip() and len(cleaned) >= 20:
                session.execute(
                    Article.__table__.update()
                    .where(Article.id == article_id)
                    .values(summary=cleaned)
                )
                cleaned_count += 1
            else:
                # Cleaning didn't help enough — re-dispatch full LLM summarization
                app.send_task("summarize_article", args=[str(user_id), str(article_id)])
                resend_count += 1

        logger.info(
            f"reprocess_summaries: cleaned {cleaned_count} in-place, "
            f"re-dispatched {resend_count} for LLM re-summarization "
            f"(total {len(dirty_articles)} dirty summaries found)"
        )

    return {
        "dirty_found": len(dirty_articles),
        "cleaned_in_place": cleaned_count,
        "re_dispatched": resend_count,
    }
