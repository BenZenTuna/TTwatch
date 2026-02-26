"""Maintenance and market data tasks — cleanup, data retention, and market data fetching."""
import logging
from sqlalchemy import text, select
from worker.celeryconfig import app
from worker.db import db_session

logger = logging.getLogger(__name__)


@app.task(name="cleanup_stale_market_data")
def cleanup_stale_market_data():
    """Delete market data older than 30 days. Keep one snapshot per day per symbol.

    Uses a CTE to identify rows to keep, then deletes the rest. This is more
    efficient than NOT IN with DISTINCT ON, which forces a full subquery scan.
    """
    with db_session() as session:
        session.execute(text("""
            WITH keep AS (
                SELECT DISTINCT ON (symbol, date_trunc('day', fetched_at))
                    id
                FROM market_data_cache
                WHERE fetched_at < now() - interval '30 days'
                ORDER BY symbol, date_trunc('day', fetched_at), fetched_at DESC
            )
            DELETE FROM market_data_cache
            WHERE fetched_at < now() - interval '30 days'
            AND id NOT IN (SELECT id FROM keep)
        """))


@app.task(name="cleanup_stale_snapshots")
def cleanup_stale_snapshots():
    """Delete old briefings and investment analyses beyond retention window.

    Keeps the 10 most recent briefings per user per topic, and
    analyses from the last 90 days.
    """
    with db_session() as session:
        # Clean old briefings: keep latest 10 per user/topic.
        # Uses CTE to identify rows to keep, then DELETE excludes them.
        session.execute(text("""
            WITH keep AS (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY user_id, topic_id
                        ORDER BY generated_at DESC
                    ) AS rn
                    FROM briefings
                ) sub
                WHERE rn <= 10
            )
            DELETE FROM briefings
            WHERE id NOT IN (SELECT id FROM keep)
            AND generated_at < now() - interval '7 days'
        """))

        # Clean old investment analyses: keep last 90 days
        session.execute(text("""
            DELETE FROM investment_analyses
            WHERE generated_at < now() - interval '90 days'
        """))


@app.task(name="fetch_market_data")
def fetch_market_data(symbol: str):
    """Fetch current market data for a single symbol and cache it.

    Dispatched by refresh_market_data periodic task for each watched symbol.
    Uses yfinance for equities and CoinGecko for crypto.

    Handles the market_data_cache dedup UNIQUE index by using ON CONFLICT
    (same symbol + same hour → update existing row instead of crash).
    """
    import httpx
    from sqlalchemy import text as sa_text

    with db_session() as session:
        from app.models import MarketDataCache, TickerReference

        # Determine asset type from ticker_reference
        ref = session.execute(
            select(TickerReference).where(TickerReference.symbol == symbol)
        ).scalar_one_or_none()
        asset_type = ref.asset_type if ref else "equity"

        try:
            cache_data = {
                "symbol": symbol,
                "asset_type": asset_type,
                "data_source": "yfinance",
                "price": None,
                "price_change_pct": None,
                "volume": None,
                "market_cap": None,
                "pe_ratio": None,
                "eps": None,
                "dividend_yield": None,
                "beta": None,
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
            }

            if asset_type == "crypto":
                # CoinGecko uses its own IDs (e.g., "bitcoin" not "BTC").
                # Check ticker_reference metadata for coingecko_id, else
                # fall back to symbol lowercase (works for many: "ethereum", "solana").
                cg_id = None
                if ref and ref.metadata_:
                    cg_id = ref.metadata_.get("coingecko_id")
                if not cg_id:
                    cg_id = symbol.lower()

                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(
                        "https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": cg_id, "vs_currencies": "usd",
                                "include_24hr_change": "true", "include_market_cap": "true",
                                "include_24hr_vol": "true"},
                    )
                    resp.raise_for_status()
                    data = resp.json().get(cg_id, {})
                    cache_data.update({
                        "price": data.get("usd"),
                        "price_change_pct": data.get("usd_24h_change"),
                        "market_cap": data.get("usd_market_cap"),
                        "volume": int(data["usd_24h_vol"]) if data.get("usd_24h_vol") else None,
                        "data_source": "coingecko",
                    })
            else:
                # Use yfinance for equities/ETFs — populate all available fields
                import yfinance as yf
                ticker = yf.Ticker(symbol)
                info = ticker.info
                cache_data.update({
                    "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "price_change_pct": info.get("regularMarketChangePercent"),
                    "market_cap": info.get("marketCap"),
                    "volume": info.get("regularMarketVolume"),
                    "pe_ratio": info.get("trailingPE"),
                    "eps": info.get("trailingEps"),
                    "dividend_yield": info.get("dividendYield"),
                    "beta": info.get("beta"),
                    "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                    "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                })

            if cache_data.get("price") is not None:
                # Use raw SQL upsert to handle the dedup UNIQUE index.
                # ON CONFLICT on (symbol, date_trunc('hour', fetched_at))
                # updates the existing row instead of crashing.
                session.execute(sa_text("""
                    INSERT INTO market_data_cache
                        (id, symbol, asset_type, price, price_change_pct, volume,
                         market_cap, pe_ratio, eps, dividend_yield, beta,
                         fifty_two_week_high, fifty_two_week_low, data_source, fetched_at)
                    VALUES
                        (gen_random_uuid(), :symbol, :asset_type, :price, :price_change_pct,
                         :volume, :market_cap, :pe_ratio, :eps, :dividend_yield, :beta,
                         :fifty_two_week_high, :fifty_two_week_low, :data_source, now())
                    ON CONFLICT (symbol, date_trunc('hour', fetched_at))
                    DO UPDATE SET
                        price = EXCLUDED.price,
                        price_change_pct = EXCLUDED.price_change_pct,
                        volume = EXCLUDED.volume,
                        market_cap = EXCLUDED.market_cap,
                        pe_ratio = EXCLUDED.pe_ratio,
                        eps = EXCLUDED.eps,
                        dividend_yield = EXCLUDED.dividend_yield,
                        beta = EXCLUDED.beta,
                        fifty_two_week_high = EXCLUDED.fifty_two_week_high,
                        fifty_two_week_low = EXCLUDED.fifty_two_week_low,
                        data_source = EXCLUDED.data_source,
                        is_stale = false
                """), cache_data)
                logger.info(f"Market data cached for {symbol}: ${cache_data['price']}")
        except Exception as e:
            logger.warning(f"Failed to fetch market data for {symbol}: {e}")


@app.task(name="cleanup_expired_refresh_tokens")
def cleanup_expired_refresh_tokens():
    """Delete expired refresh tokens from the database.

    Refresh tokens have a 30-day expiry (REFRESH_TOKEN_EXPIRE). The login handler
    caps active tokens at 10 per user, but expired tokens are never removed —
    they just fail the expires_at check on refresh. Over time, the refresh_tokens
    table grows unbounded. This task deletes all tokens past their expiry date.

    Runs daily. Safe to run concurrently — DELETE with WHERE is idempotent.
    """
    with db_session() as session:
        result = session.execute(text("""
            DELETE FROM refresh_tokens
            WHERE expires_at < now()
        """))
        logger.info(f"Cleaned up {result.rowcount} expired refresh tokens")


@app.task(name="cleanup_orphaned_qdrant_points")
def cleanup_orphaned_qdrant_points():
    """Remove Qdrant points whose corresponding PostgreSQL articles no longer exist.

    Over time, article/topic deletions leave orphaned vectors in Qdrant because
    PostgreSQL CASCADE deletes don't propagate to Qdrant. These orphaned points
    inflate cluster article_count, waste storage, and degrade search quality.

    Runs daily. Scrolls all Qdrant points and batch-checks existence in PostgreSQL.
    """
    import os
    from qdrant_client import QdrantClient
    from app.models import Article

    qdrant = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))

    # Scroll all points in batches
    orphaned_ids = []
    offset = None
    while True:
        points, next_offset = qdrant.scroll(
            collection_name="articles",
            offset=offset,
            limit=500,
            with_vectors=False,
            with_payload=False,
        )
        if not points:
            break

        point_ids = [str(p.id) for p in points]

        with db_session() as session:
            existing = set(
                str(row[0]) for row in session.execute(
                    select(Article.id).where(Article.id.in_(point_ids))
                ).all()
            )

        orphans = [pid for pid in point_ids if pid not in existing]
        orphaned_ids.extend(orphans)

        if next_offset is None:
            break
        offset = next_offset

    if orphaned_ids:
        # Delete in batches of 500
        for i in range(0, len(orphaned_ids), 500):
            batch = orphaned_ids[i:i + 500]
            qdrant.delete(
                collection_name="articles",
                points_selector=batch,
            )
        logger.info(f"Removed {len(orphaned_ids)} orphaned Qdrant points")
    else:
        logger.info("No orphaned Qdrant points found")
