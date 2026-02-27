"""Route LLM calls to the appropriate model based on per-user task config.

Each task defines a TASK_CATEGORY constant. At runtime, get_llm_for_task()
checks the user's llm_task_config row and returns either the primary or
fast LLM client accordingly.
"""
import logging

from sqlalchemy import text

from worker.llm_sync import SyncLLMClient, create_fast_client

logger = logging.getLogger(__name__)

# Module-level singletons — created once per worker process
_primary = SyncLLMClient()
_fast = create_fast_client()

# Default routing: ALL tasks default to fast model
_DEFAULT_TARGETS: dict[str, str] = {
    "summarization": "fast",
    "sentiment": "fast",
    "relevance": "fast",
    "entity_extraction": "fast",
    "ticker_resolution": "fast",
    "briefing": "fast",
    "investment_analysis": "fast",
    "coverage_gaps": "fast",
    "search_planning": "fast",
    "correlation": "fast",
}


def get_llm_for_task(session, user_id: str, task_category: str) -> SyncLLMClient:
    """Return the appropriate LLM client based on user config.

    1. Queries llm_task_config for the user + task_category
    2. Falls back to hardcoded default if no row exists
    3. Returns _fast for 'fast', _primary for 'primary'
    4. For 'auto': returns _fast (with _primary as implicit fallback
       since create_fast_client() already falls back on construction)
    """
    target = _DEFAULT_TARGETS.get(task_category, "fast")

    try:
        row = session.execute(
            text(
                "SELECT model_target FROM llm_task_config "
                "WHERE user_id = :uid AND task_category = :cat"
            ),
            {"uid": user_id, "cat": task_category},
        ).first()
        if row:
            target = row[0]
    except Exception:
        # Table may not exist yet (pre-migration) — use default
        logger.debug(f"Could not query llm_task_config for {task_category}, using default")

    if target == "primary":
        return _primary
    elif target == "fast":
        return _fast
    else:
        # 'auto' — prefer fast, it already falls back to primary if unavailable
        return _fast
