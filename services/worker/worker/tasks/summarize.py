"""Summarize article text using LLM."""
import logging

from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from worker.tasks.utils import fetch_article_text
from app.models import Article

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="summarize_article", max_retries=3, default_retry_delay=30)
@with_rls_context
def summarize_article(user_id: str, article_id: str, session=None):
    """Generate a 2-sentence summary (100-200 tokens) and store on article."""
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    summary = _llm.generate([
        {"role": "system", "content": "Summarize this article in 2 sentences."},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ], max_tokens=200)
    article.summary = summary

    logger.info(f"Summarized article {article_id}: {article.title[:60]}")
