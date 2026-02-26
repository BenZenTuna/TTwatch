"""Generate investment analyses for entities with resolved ticker symbols."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import (
    AssetMapping, Article, Entity, EntityArticleMap,
    MarketDataCache, InvestmentAnalysis,
)

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="generate_investment_analyses", max_retries=2, default_retry_delay=60)
@with_rls_context
def generate_investment_analyses(user_id: str, topic_id: str, session=None):
    """Generate investment analyses for entities with resolved tickers.

    For each asset mapping in the topic, gathers recent article summaries
    mentioning that entity, fetches latest market data, and generates
    an LLM-powered analysis with sentiment and key signals.
    """
    mappings = session.execute(
        select(AssetMapping).where(
            AssetMapping.topic_id == topic_id,
            AssetMapping.resolved_symbol.isnot(None),
        )
    ).scalars().all()

    for mapping in mappings:
        # Get recent articles mentioning this entity
        article_summaries = session.execute(
            select(Article.title, Article.summary, Article.sentiment_score).join(
                EntityArticleMap, EntityArticleMap.article_id == Article.id
            ).where(
                EntityArticleMap.entity_id == mapping.entity_id,
                Article.summary.isnot(None),
                Article.is_duplicate == False,
            ).order_by(Article.ingested_at.desc()).limit(15)
        ).all()

        if not article_summaries:
            continue

        # Get latest market data
        market_data = session.execute(
            select(MarketDataCache).where(
                MarketDataCache.symbol == mapping.resolved_symbol
            ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
        ).scalar_one_or_none()

        news_context = "\n".join(
            f"- {title}: {summary} (sentiment: {score:.2f})"
            for title, summary, score in article_summaries
            if summary and score is not None
        )

        market_context = ""
        if market_data:
            market_context = (
                f"Price: ${market_data.price}, "
                f"Change: {market_data.price_change_pct}%, "
                f"Market Cap: {market_data.market_cap}"
            )

        result = _llm.generate_json([
            {"role": "system", "content": (
                "You are a financial analyst. Analyze the news sentiment and market data "
                "for this asset. Return JSON: {\"analysis\": \"2-3 paragraph analysis\", "
                "\"recommendation\": \"bullish|bearish|neutral\", \"confidence\": 0.0-1.0, "
                "\"key_signals\": [\"signal 1\", ...], \"risk_factors\": [\"risk 1\", ...]}"
            )},
            {"role": "user", "content": (
                f"Asset: {mapping.entity_name} ({mapping.resolved_symbol})\n"
                f"Market Data: {market_context}\n\n"
                f"Recent News:\n{news_context}"
            )},
        ])

        # Calculate aggregate sentiment from articles
        sentiments = [s for _, _, s in article_summaries if s is not None]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        analysis = InvestmentAnalysis(
            user_id=user_id,
            topic_id=topic_id,
            analysis_scope="asset",
            scope_ref_id=mapping.id,
            symbol=mapping.resolved_symbol,
            analysis_text=result.get("analysis", ""),
            recommendation=result.get("recommendation", "neutral"),
            confidence=max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
            key_signals=result.get("key_signals", []),
            risk_factors=result.get("risk_factors", []),
            articles_considered=len(article_summaries),
            market_data_cache_id=market_data.id if market_data else None,
            sentiment_score=avg_sentiment,
            model_used=_llm.model,
        )
        session.add(analysis)

    logger.info(f"Generated investment analyses for {len(mappings)} assets in topic {topic_id}")
