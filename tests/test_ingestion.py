"""Integration tests for article ingestion and deduplication.

Tests the article listing/detail API endpoints. The actual ingestion
pipeline runs via Celery tasks which are tested separately.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestArticleListing:
    async def test_list_articles_empty(self, client: AsyncClient, auth_headers, test_topic):
        """List articles for topic with no articles."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}/articles",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_articles(self, client: AsyncClient, auth_headers, test_topic, test_article):
        """List articles returns articles for the topic."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}/articles",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Article"
        assert data[0]["url"] == "https://example.com/article-1"

    async def test_list_articles_pagination(
        self, client: AsyncClient, auth_headers, test_topic, test_user, db_session
    ):
        """Articles support offset/limit pagination."""
        from app.models.intelligence import Article

        for i in range(5):
            a = Article(
                id=uuid.uuid4(),
                user_id=test_user.id,
                topic_id=test_topic.id,
                url=f"https://example.com/page-{i}",
                title=f"Article {i}",
                is_duplicate=False,
            )
            db_session.add(a)
        await db_session.flush()

        resp = await client.get(
            f"/api/topics/{test_topic.id}/articles",
            headers=auth_headers,
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        resp2 = await client.get(
            f"/api/topics/{test_topic.id}/articles",
            headers=auth_headers,
            params={"limit": 2, "offset": 2},
        )
        assert resp2.status_code == 200
        assert len(resp2.json()) == 2

    async def test_list_articles_filter_duplicates(
        self, client: AsyncClient, auth_headers, test_topic, test_user, db_session
    ):
        """Filter articles by is_duplicate."""
        from app.models.intelligence import Article

        a_orig = Article(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            url="https://example.com/original",
            title="Original",
            is_duplicate=False,
        )
        a_dup = Article(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            url="https://example.com/duplicate",
            title="Duplicate",
            is_duplicate=True,
        )
        db_session.add_all([a_orig, a_dup])
        await db_session.flush()

        resp = await client.get(
            f"/api/topics/{test_topic.id}/articles",
            headers=auth_headers,
            params={"is_duplicate": False},
        )
        assert resp.status_code == 200
        titles = [a["title"] for a in resp.json()]
        assert "Original" in titles
        assert "Duplicate" not in titles


@pytest.mark.asyncio
class TestArticleDetail:
    async def test_get_article(self, client: AsyncClient, auth_headers, test_article):
        """Get a single article by ID."""
        resp = await client.get(
            f"/api/articles/{test_article.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Article"
        assert data["summary"] == "A test article summary."

    async def test_get_article_not_found(self, client: AsyncClient, auth_headers):
        """404 for nonexistent article."""
        resp = await client.get(
            f"/api/articles/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestArticleIsolation:
    async def test_cannot_see_other_users_articles(
        self, client: AsyncClient, auth_headers, db_session
    ):
        """User cannot access another user's article."""
        from app.models.user import User
        from app.models.intelligence import Topic, Article
        from argon2 import PasswordHasher

        ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        other = User(
            id=uuid.uuid4(),
            email="article-other@example.com",
            display_name="Other",
            password_hash=ph.hash("OtherPass123!"),
        )
        db_session.add(other)
        await db_session.flush()

        topic = Topic(id=uuid.uuid4(), user_id=other.id, name="Other Topic", config={})
        db_session.add(topic)
        await db_session.flush()

        article = Article(
            id=uuid.uuid4(),
            user_id=other.id,
            topic_id=topic.id,
            url="https://example.com/secret",
            title="Secret Article",
            is_duplicate=False,
        )
        db_session.add(article)
        await db_session.flush()

        resp = await client.get(
            f"/api/articles/{article.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDedupLogic:
    async def test_url_uniqueness_per_user_topic(self, db_session, test_user, test_topic):
        """Same URL cannot be inserted twice for the same user+topic."""
        from app.models.intelligence import Article
        from sqlalchemy.exc import IntegrityError

        a1 = Article(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            url="https://example.com/same-url",
            title="First",
            is_duplicate=False,
        )
        db_session.add(a1)
        await db_session.flush()

        a2 = Article(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            url="https://example.com/same-url",
            title="Second",
            is_duplicate=False,
        )
        db_session.add(a2)
        with pytest.raises(IntegrityError):
            await db_session.flush()
