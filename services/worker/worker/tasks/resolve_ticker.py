"""Resolve named entities to ticker symbols using hardcoded map, fuzzy DB match, then LLM fallback."""
import logging
from sqlalchemy import select, text
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import create_fast_client
from app.models import Entity, TickerReference, AssetMapping

logger = logging.getLogger(__name__)

_llm = create_fast_client()

# Fast first-pass: common entity name → ticker mappings
_COMMON_TICKERS = {
    "nvidia": "NVDA",
    "apple": "AAPL",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "intel": "INTC",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "taiwan semiconductor": "TSM",
    "tsmc": "TSM",
    "samsung": "005930.KS",
    "ibm": "IBM",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "palantir": "PLTR",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "bitcoin": "BTC-USD",
    "ethereum": "ETH-USD",
    "solana": "SOL-USD",
    "ripple": "XRP-USD",
    "xrp": "XRP-USD",
    "dogecoin": "DOGE-USD",
    "cardano": "ADA-USD",
}


@app.task(name="resolve_entity_ticker", max_retries=2, default_retry_delay=30)
@with_rls_context
def resolve_entity_ticker(user_id: str, entity_id: str, topic_id: str, session=None):
    """Resolve a named entity (e.g., 'Tesla', 'Bitcoin') to a ticker symbol.

    Uses a three-step approach:
    1. Hardcoded common mappings (instant, no DB or LLM).
    2. pg_trgm fuzzy match against ticker_reference (fast DB query).
    3. Fall back to fast LLM for inference.

    Creates an AssetMapping record linking the entity to the resolved symbol.
    """
    entity = session.execute(
        select(Entity).where(Entity.id == entity_id)
    ).scalar_one_or_none()
    if not entity:
        return

    if entity.type not in ("org", "product", "technology"):
        return

    existing = session.execute(
        select(AssetMapping).where(
            AssetMapping.user_id == user_id,
            AssetMapping.entity_id == entity_id,
        )
    ).scalar_one_or_none()
    if existing:
        return

    entity_lower = entity.name.strip().lower()

    # Step 1: Hardcoded common mappings
    if entity_lower in _COMMON_TICKERS:
        symbol = _COMMON_TICKERS[entity_lower]
        session.add(AssetMapping(
            user_id=user_id,
            topic_id=topic_id,
            entity_id=entity_id,
            entity_name=entity.name,
            resolved_symbol=symbol,
            resolution_method="hardcoded_lookup",
            confidence=0.95,
        ))
        logger.info(f"Resolved '{entity.name}' -> {symbol} via hardcoded lookup")
        return

    # Step 2: pg_trgm fuzzy match against ticker_reference
    fuzzy_result = session.execute(
        text(
            "SELECT id, symbol, name, similarity(name, :name) AS sim "
            "FROM ticker_reference "
            "WHERE is_active = true AND similarity(name, :name) > 0.3 "
            "ORDER BY sim DESC LIMIT 1"
        ),
        {"name": entity.name},
    ).first()

    if fuzzy_result:
        session.add(AssetMapping(
            user_id=user_id,
            topic_id=topic_id,
            entity_id=entity_id,
            ticker_ref_id=str(fuzzy_result.id),
            entity_name=entity.name,
            resolved_symbol=fuzzy_result.symbol,
            resolution_method="fuzzy_lookup",
            confidence=min(0.95, float(fuzzy_result.sim)),
        ))
        logger.info(
            f"Resolved '{entity.name}' -> {fuzzy_result.symbol} "
            f"via fuzzy match (sim={fuzzy_result.sim:.2f})"
        )
        return

    # Step 3: LLM resolution (fast model)
    result = _llm.generate_json([
        {"role": "system", "content": (
            "Given the entity name, determine if it corresponds to a publicly "
            "traded stock, ETF, or cryptocurrency. "
            "Respond with only the requested format. Do not include explanations. "
            "Return JSON: "
            '{"symbol": "TICKER", "asset_type": "equity|etf|crypto", "confidence": 0.0-1.0}. '
            'If you cannot determine a ticker, return {"symbol": null, "confidence": 0.0}.'
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
        logger.info(f"Resolved '{entity.name}' -> {symbol} via LLM (confidence={confidence:.2f})")
