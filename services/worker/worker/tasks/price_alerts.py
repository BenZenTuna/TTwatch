"""Check price alerts against latest market data and trigger notifications."""
import logging
import json
import os
import redis as redis_lib
from datetime import datetime, timezone
from sqlalchemy import select
from worker.celeryconfig import app
from worker.db import db_session
from app.models import PriceAlert, MarketDataCache

logger = logging.getLogger(__name__)

# Redis pub/sub for real-time WebSocket notifications.
# Workers are synchronous and cannot access the API's in-process ws_manager.
# Instead, triggered alerts are published to a Redis channel that the API's
# WebSocket background listener subscribes to (see main.py ws_alert_listener).
_alert_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)


@app.task(name="check_price_alerts")
def check_price_alerts():
    """Check all active price alerts against latest market data.

    For each active alert, compares the latest cached price against the
    alert threshold. Triggered alerts are marked with triggered_at and
    deactivated. Publishes triggered alerts to Redis pub/sub channel
    'ttwatch:alerts:triggered' for real-time WebSocket delivery to users.
    """
    with db_session() as session:
        active_alerts = session.execute(
            select(PriceAlert).where(PriceAlert.is_active == True)
        ).scalars().all()

        triggered_count = 0
        for alert in active_alerts:
            # Get latest price for this symbol
            market = session.execute(
                select(MarketDataCache).where(
                    MarketDataCache.symbol == alert.symbol,
                ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
            ).scalar_one_or_none()

            if not market or market.price is None:
                continue

            price = float(market.price)
            threshold = float(alert.threshold)
            triggered = False

            if alert.condition == "above" and price >= threshold:
                triggered = True
            elif alert.condition == "below" and price <= threshold:
                triggered = True
            elif alert.condition == "crosses_above":
                # Only triggers if the last known price was BELOW threshold
                # and current price is at or above. On first check after alert
                # creation (last_known_price is NULL), initialize it from
                # current price without triggering — this establishes the
                # baseline for subsequent crossing detection.
                if alert.last_known_price is not None:
                    was_below = float(alert.last_known_price) < threshold
                    if was_below and price >= threshold:
                        triggered = True
                # else: first check — will be initialized below
            elif alert.condition == "crosses_below":
                # Only triggers if the last known price was ABOVE threshold
                # and current price is at or below. Same first-check logic.
                if alert.last_known_price is not None:
                    was_above = float(alert.last_known_price) > threshold
                    if was_above and price <= threshold:
                        triggered = True

            # Always update last_known_price for crosses conditions.
            # This handles both normal updates AND first-check initialization
            # (when last_known_price was NULL from alert creation).
            if alert.condition in ("crosses_above", "crosses_below"):
                alert.last_known_price = price

            if triggered:
                alert.is_active = False
                alert.triggered_at = datetime.now(timezone.utc)
                triggered_count += 1
                logger.info(
                    f"Price alert triggered: {alert.symbol} {alert.condition} "
                    f"${threshold} (current: ${price})"
                )
                # Publish to Redis for real-time WebSocket delivery.
                # The API's ws_alert_listener coroutine subscribes to this
                # channel and forwards to the user's WebSocket connections.
                try:
                    _alert_redis.publish("ttwatch:alerts:triggered", json.dumps({
                        "user_id": str(alert.user_id),
                        "type": "price_alert_triggered",
                        "symbol": alert.symbol,
                        "condition": alert.condition,
                        "threshold": float(threshold),
                        "price": price,
                        "alert_id": str(alert.id),
                    }))
                except Exception as e:
                    logger.warning(f"Failed to publish alert notification: {e}")

        logger.info(f"Price alert check: {triggered_count}/{len(active_alerts)} triggered")
