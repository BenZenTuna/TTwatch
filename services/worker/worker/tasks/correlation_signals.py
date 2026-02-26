"""Detect correlation signals between news sentiment and price movements."""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import (
    Article, AssetMapping, MarketDataCache,
    CorrelationSignal, EntityArticleMap,
)

logger = logging.getLogger(__name__)


@app.task(name="detect_correlation_signals", max_retries=2, default_retry_delay=60)
@with_rls_context
def detect_correlation_signals(user_id: str, topic_id: str, session=None):
    """Detect correlations between news sentiment shifts and price movements.

    For each resolved asset mapping, compares recent sentiment trends with
    recent price changes to identify potential leading/lagging indicators.
    """
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(hours=48)

    mappings = session.execute(
        select(AssetMapping).where(
            AssetMapping.topic_id == topic_id,
            AssetMapping.resolved_symbol.isnot(None),
        )
    ).scalars().all()

    for mapping in mappings:
        # Calculate sentiment trend over last 48h
        sentiment_agg = session.execute(
            select(func.avg(Article.sentiment_score)).join(
                EntityArticleMap, EntityArticleMap.article_id == Article.id
            ).where(
                EntityArticleMap.entity_id == mapping.entity_id,
                Article.sentiment_score.isnot(None),
                Article.ingested_at >= lookback,
                Article.is_duplicate == False,
            )
        ).scalar()

        if sentiment_agg is None:
            continue

        # Get latest market data
        market = session.execute(
            select(MarketDataCache).where(
                MarketDataCache.symbol == mapping.resolved_symbol,
            ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
        ).scalar_one_or_none()

        if not market or market.price_change_pct is None:
            continue

        price_change = float(market.price_change_pct)
        avg_sentiment = float(sentiment_agg)

        # Detect divergence signals
        signal_type = None
        signal_strength = 0.0

        if avg_sentiment > 0.3 and price_change < -2.0:
            signal_type = "sentiment_price_divergence_bullish"
            signal_strength = min(1.0, abs(avg_sentiment - price_change / 100) / 0.5)
        elif avg_sentiment < -0.3 and price_change > 2.0:
            signal_type = "sentiment_price_divergence_bearish"
            signal_strength = min(1.0, abs(avg_sentiment - price_change / 100) / 0.5)
        elif avg_sentiment > 0.5 and price_change > 3.0:
            signal_type = "momentum_confirmation_bullish"
            signal_strength = min(1.0, (avg_sentiment + price_change / 100) / 1.0)
        elif avg_sentiment < -0.5 and price_change < -3.0:
            signal_type = "momentum_confirmation_bearish"
            signal_strength = min(1.0, abs(avg_sentiment + price_change / 100) / 1.0)

        if signal_type and signal_strength >= 0.3:
            session.add(CorrelationSignal(
                user_id=user_id,
                topic_id=topic_id,
                symbol=mapping.resolved_symbol,
                signal_type=signal_type,
                signal_strength=signal_strength,
                description=(
                    f"Sentiment={avg_sentiment:.2f}, "
                    f"Price change={price_change:.1f}%"
                ),
            ))

    logger.info(f"Correlation signal scan complete for topic {topic_id}")
