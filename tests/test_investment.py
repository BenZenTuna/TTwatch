"""Integration tests for investment: watchlist CRUD, price alert lifecycle."""
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestWatchlistCRUD:
    async def test_list_watchlist_empty(self, client: AsyncClient, auth_headers, test_topic):
        """List watchlist returns empty for topic with no items."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}/watchlist",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_add_watchlist_item(self, client: AsyncClient, auth_headers, test_topic):
        """Add an item to the watchlist."""
        resp = await client.post(
            f"/api/topics/{test_topic.id}/watchlist",
            headers=auth_headers,
            json={
                "symbol": "AAPL",
                "asset_type": "equity",
                "added_reason": "Top holding in tech sector",
                "notes": "Watch for earnings",
                "target_price": "200.50",
                "stop_loss": "150.00",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert data["asset_type"] == "equity"
        assert data["added_reason"] == "Top holding in tech sector"
        assert "id" in data

    async def test_add_duplicate_symbol(self, client: AsyncClient, auth_headers, test_topic, test_user, db_session):
        """Cannot add same symbol twice."""
        from app.models.investment import WatchlistItem

        item = WatchlistItem(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            symbol="MSFT",
            asset_type="equity",
        )
        db_session.add(item)
        await db_session.flush()

        resp = await client.post(
            f"/api/topics/{test_topic.id}/watchlist",
            headers=auth_headers,
            json={"symbol": "MSFT", "asset_type": "equity"},
        )
        assert resp.status_code == 409

    async def test_remove_watchlist_item(self, client: AsyncClient, auth_headers, test_topic, test_user, db_session):
        """Remove an item from the watchlist."""
        from app.models.investment import WatchlistItem

        item = WatchlistItem(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            symbol="GOOG",
            asset_type="equity",
        )
        db_session.add(item)
        await db_session.flush()

        resp = await client.delete(
            f"/api/watchlist/{item.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    async def test_remove_nonexistent_item(self, client: AsyncClient, auth_headers):
        """Remove nonexistent watchlist item returns 404."""
        resp = await client.delete(
            f"/api/watchlist/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestWatchlistIsolation:
    async def test_cannot_delete_other_users_item(
        self, client: AsyncClient, auth_headers, db_session, test_topic
    ):
        """User cannot delete another user's watchlist item."""
        from app.models.user import User
        from app.models.investment import WatchlistItem
        from argon2 import PasswordHasher

        ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        other = User(
            id=uuid.uuid4(),
            email="inv-other@example.com",
            display_name="Other",
            password_hash=ph.hash("OtherPass123!"),
        )
        db_session.add(other)
        await db_session.flush()

        item = WatchlistItem(
            id=uuid.uuid4(),
            user_id=other.id,
            symbol="TSLA",
            asset_type="equity",
        )
        db_session.add(item)
        await db_session.flush()

        resp = await client.delete(
            f"/api/watchlist/{item.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestPriceAlertLifecycle:
    async def test_create_price_alert(self, client: AsyncClient, auth_headers):
        """Create a new price alert."""
        resp = await client.post("/api/price-alerts", headers=auth_headers, json={
            "symbol": "NVDA",
            "condition": "above",
            "threshold": "150.00",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["symbol"] == "NVDA"
        assert data["condition"] == "above"
        assert data["is_active"] is True
        assert data["triggered_at"] is None

    async def test_create_alert_invalid_condition(self, client: AsyncClient, auth_headers):
        """Reject invalid alert condition."""
        resp = await client.post("/api/price-alerts", headers=auth_headers, json={
            "symbol": "NVDA",
            "condition": "invalid_op",
            "threshold": "100.00",
        })
        assert resp.status_code == 400

    async def test_list_price_alerts(self, client: AsyncClient, auth_headers, test_user, db_session):
        """List active price alerts."""
        from app.models.investment import PriceAlert

        alert = PriceAlert(
            id=uuid.uuid4(),
            user_id=test_user.id,
            symbol="AMD",
            condition="below",
            threshold=100,
            is_active=True,
        )
        db_session.add(alert)
        await db_session.flush()

        resp = await client.get("/api/price-alerts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        symbols = [a["symbol"] for a in data]
        assert "AMD" in symbols

    async def test_delete_price_alert(self, client: AsyncClient, auth_headers, test_user, db_session):
        """Delete a price alert."""
        from app.models.investment import PriceAlert

        alert = PriceAlert(
            id=uuid.uuid4(),
            user_id=test_user.id,
            symbol="INTC",
            condition="crosses_above",
            threshold=50,
            is_active=True,
        )
        db_session.add(alert)
        await db_session.flush()

        resp = await client.delete(
            f"/api/price-alerts/{alert.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    async def test_delete_alert_not_found(self, client: AsyncClient, auth_headers):
        """Delete nonexistent alert returns 404."""
        resp = await client.delete(
            f"/api/price-alerts/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_alert_isolation(self, client: AsyncClient, auth_headers, db_session):
        """User cannot delete another user's alert."""
        from app.models.user import User
        from app.models.investment import PriceAlert
        from argon2 import PasswordHasher

        ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        other = User(
            id=uuid.uuid4(),
            email="alert-other@example.com",
            display_name="Other",
            password_hash=ph.hash("OtherPass123!"),
        )
        db_session.add(other)
        await db_session.flush()

        alert = PriceAlert(
            id=uuid.uuid4(),
            user_id=other.id,
            symbol="BTC",
            condition="above",
            threshold=100000,
            is_active=True,
        )
        db_session.add(alert)
        await db_session.flush()

        resp = await client.delete(
            f"/api/price-alerts/{alert.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestInvestmentAnalyses:
    async def test_list_analyses_empty(self, client: AsyncClient, auth_headers, test_topic):
        """List analyses returns empty for topic with no analyses."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}/analyses",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_analyses(self, client: AsyncClient, auth_headers, test_topic, test_user, db_session):
        """List investment analyses for a topic."""
        from app.models.investment import InvestmentAnalysis

        analysis = InvestmentAnalysis(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            analysis_scope="asset",
            symbol="AAPL",
            analysis_text="Apple shows strong momentum...",
            recommendation="buy",
            confidence=0.8,
            key_signals=["earnings beat"],
            risk_factors=["valuation"],
            articles_considered=15,
        )
        db_session.add(analysis)
        await db_session.flush()

        resp = await client.get(
            f"/api/topics/{test_topic.id}/analyses",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "AAPL"
        assert data[0]["recommendation"] == "buy"


@pytest.mark.asyncio
class TestCorrelationSignals:
    async def test_list_signals_empty(self, client: AsyncClient, auth_headers, test_topic):
        """List correlation signals returns empty for topic with no signals."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}/correlation-signals",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_signals(self, client: AsyncClient, auth_headers, test_topic, test_user, db_session):
        """List correlation signals for a topic."""
        from app.models.investment import CorrelationSignal

        signal = CorrelationSignal(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            symbol="NVDA",
            signal_type="sentiment_price_divergence",
            signal_strength=0.85,
            description="Positive sentiment but price declining",
        )
        db_session.add(signal)
        await db_session.flush()

        resp = await client.get(
            f"/api/topics/{test_topic.id}/correlation-signals",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "NVDA"
        assert data[0]["signal_type"] == "sentiment_price_divergence"
