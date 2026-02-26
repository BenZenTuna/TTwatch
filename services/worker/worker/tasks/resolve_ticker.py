"""Resolve named entities to ticker symbols using LLM + ticker_reference lookup."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import Entity, TickerReference, AssetMapping

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="resolve_entity_ticker", max_retries=2, default_retry_delay=30)
@with_rls_context
def resolve_entity_ticker(user_id: str, entity_id: str, topic_id: str, session=None):
    """Resolve a named entity (e.g., 'Tesla', 'Bitcoin') to a ticker symbol.

    Uses a two-step approach:
    1. Check ticker_reference for direct name match (fast, no LLM).
    2. If no match, use LLM to infer the most likely ticker symbol.

    Creates an AssetMapping record linking the entity to the resolved symbol.
    """
    entity = session.execute(
        select(Entity).where(Entity.id == entity_id)
    ).scalar_one_or_none()
    if not entity:
        return

    # Skip non-resolvable entity types
    if entity.type not in ("org", "product", "technology"):
        return

    # Check if already resolved
    existing = session.execute(
        select(AssetMapping).where(
            AssetMapping.user_id == user_id,
            AssetMapping.entity_id == entity_id,
        )
    ).scalar_one_or_none()
    if existing:
        return

    # Step 1: Direct lookup in ticker_reference
    ref = session.execute(
        select(TickerReference).where(
            TickerReference.name.ilike(f"%{entity.name}%"),
            TickerReference.is_active == True,
        ).limit(1)
    ).scalar_one_or_none()

    if ref:
        session.add(AssetMapping(
            user_id=user_id,
            topic_id=topic_id,
            entity_id=entity_id,
            ticker_ref_id=ref.id,
            entity_name=entity.name,
            resolved_symbol=ref.symbol,
            resolution_method="reference_lookup",
            confidence=0.9,
        ))
        logger.info(f"Resolved '{entity.name}' → {ref.symbol} via reference lookup")
        return

    # Step 2: LLM resolution
    result = _llm.generate_json([
        {"role": "system", "content": (
            "Given the entity name, determine if it corresponds to a publicly "
            "traded stock, ETF, or cryptocurrency. Return JSON: "
            '{"symbol": "TICKER", "asset_type": "equity|etf|crypto", "confidence": 0.0-1.0}. '
            "If you cannot determine a ticker, return {\"symbol\": null, \"confidence\": 0.0}."
        )},
        {"role": "user", "content": f"Entity: {entity.name} (type: {entity.type})"},
    ])

    symbol = result.get("symbol")
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))

    if symbol and confidence >= 0.6:
        session.add(AssetMapping(
            user_id=user_id,
            topic_id=topic_id,
            entity_id=entity_id,
            entity_name=entity.name,
            resolved_symbol=symbol.upper(),
            resolution_method="llm_inference",
            confidence=confidence,
        ))
        logger.info(f"Resolved '{entity.name}' → {symbol} via LLM (confidence={confidence:.2f})")
