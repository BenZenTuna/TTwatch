"""Integration tests for topic CRUD operations and max_topics enforcement."""
import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestTopicCRUD:
    async def test_list_topics_empty(self, client: AsyncClient, auth_headers):
        """List topics returns empty list for new user."""
        resp = await client.get("/api/topics", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_topic(self, client: AsyncClient, auth_headers):
        """Create a new topic."""
        resp = await client.post("/api/topics", headers=auth_headers, json={
            "name": "Test AI",
            "icon": "brain",
            "config": {"search_terms": ["artificial intelligence"]},
            "refresh_interval_minutes": 60,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test AI"
        assert data["icon"] == "brain"
        assert data["config"]["search_terms"] == ["artificial intelligence"]
        assert data["refresh_interval_minutes"] == 60
        assert "id" in data

    async def test_create_topic_minimal(self, client: AsyncClient, auth_headers):
        """Create a topic with only required fields."""
        resp = await client.post("/api/topics", headers=auth_headers, json={
            "name": "Minimal Topic",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Minimal Topic"
        assert data["refresh_interval_minutes"] == 120  # default

    async def test_get_topic(self, client: AsyncClient, auth_headers, test_topic):
        """Get a single topic by ID."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Topic"

    async def test_get_topic_not_found(self, client: AsyncClient, auth_headers):
        """404 for nonexistent topic."""
        fake_id = uuid.uuid4()
        resp = await client.get(f"/api/topics/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_topic(self, client: AsyncClient, auth_headers, test_topic):
        """Update a topic's name."""
        resp = await client.put(
            f"/api/topics/{test_topic.id}",
            headers=auth_headers,
            json={"name": "Updated Topic"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Topic"

    async def test_update_topic_partial(self, client: AsyncClient, auth_headers, test_topic):
        """Partial update only changes provided fields."""
        resp = await client.put(
            f"/api/topics/{test_topic.id}",
            headers=auth_headers,
            json={"icon": "rocket"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["icon"] == "rocket"
        assert data["name"] == "Test Topic"  # unchanged

    async def test_delete_topic(self, client: AsyncClient, auth_headers, test_topic):
        """Delete a topic."""
        resp = await client.delete(
            f"/api/topics/{test_topic.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Verify it's gone
        resp2 = await client.get(
            f"/api/topics/{test_topic.id}",
            headers=auth_headers,
        )
        assert resp2.status_code == 404

    async def test_delete_topic_not_found(self, client: AsyncClient, auth_headers):
        """Delete nonexistent topic returns 404."""
        fake_id = uuid.uuid4()
        resp = await client.delete(f"/api/topics/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestMaxTopicsEnforcement:
    async def test_max_topics_enforced(self, client: AsyncClient, auth_headers, test_user, db_session):
        """Cannot create topics beyond max_topics limit."""
        from app.models.intelligence import Topic

        # Set max_topics to 2 and create 2 topics
        test_user.max_topics = 2
        await db_session.flush()

        t1 = Topic(user_id=test_user.id, name="Topic 1", config={})
        t2 = Topic(user_id=test_user.id, name="Topic 2", config={})
        db_session.add_all([t1, t2])
        await db_session.flush()

        # 3rd topic should fail
        resp = await client.post("/api/topics", headers=auth_headers, json={
            "name": "Topic 3",
        })
        assert resp.status_code == 403
        assert "limit" in resp.json()["detail"].lower()


@pytest.mark.asyncio
class TestTopicIsolation:
    async def test_cannot_see_other_users_topics(self, client: AsyncClient, auth_headers, db_session):
        """User cannot access another user's topic."""
        from app.models.user import User
        from app.models.intelligence import Topic
        from argon2 import PasswordHasher

        ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        other_user = User(
            id=uuid.uuid4(),
            email="other@example.com",
            display_name="Other",
            password_hash=ph.hash("OtherPass123!"),
        )
        db_session.add(other_user)
        await db_session.flush()

        other_topic = Topic(
            id=uuid.uuid4(),
            user_id=other_user.id,
            name="Other's Topic",
            config={},
        )
        db_session.add(other_topic)
        await db_session.flush()

        # Requesting other user's topic should return 404 (not 403)
        resp = await client.get(
            f"/api/topics/{other_topic.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestTopicClusters:
    async def test_list_clusters_empty(self, client: AsyncClient, auth_headers, test_topic):
        """List clusters for topic with no clusters."""
        resp = await client.get(
            f"/api/topics/{test_topic.id}/clusters",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_clusters(self, client: AsyncClient, auth_headers, test_topic, test_user, db_session):
        """List clusters for a topic."""
        from app.models.intelligence import Cluster

        c = Cluster(
            id=uuid.uuid4(),
            user_id=test_user.id,
            topic_id=test_topic.id,
            keyword="AI safety",
            trend_score=0.85,
        )
        db_session.add(c)
        await db_session.flush()

        resp = await client.get(
            f"/api/topics/{test_topic.id}/clusters",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["keyword"] == "AI safety"
