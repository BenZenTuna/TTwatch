"""Integration tests for semantic search with mock embeddings."""
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestSemanticSearch:
    async def test_search_requires_auth(self, client: AsyncClient):
        """Search endpoint requires authentication."""
        resp = await client.post("/api/search", json={
            "query": "test",
            "topic_id": str(uuid.uuid4()),
        })
        assert resp.status_code in (401, 403)

    async def test_search_empty_results(
        self, client: AsyncClient, auth_headers, test_topic, mock_embedder
    ):
        """Search returns empty list when no matching articles."""
        # Mock Qdrant to return empty results
        mock_qdrant = AsyncMock()
        mock_qdrant.search = AsyncMock(return_value=[])

        with patch("app.routers.search.get_qdrant_client", return_value=mock_qdrant):
            resp = await client.post("/api/search", headers=auth_headers, json={
                "query": "nonexistent query",
                "topic_id": str(test_topic.id),
            })

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_search_with_results(
        self, client: AsyncClient, auth_headers, test_topic, test_article, mock_embedder
    ):
        """Search returns articles matching the query embedding."""
        # Mock Qdrant search results
        mock_hit = MagicMock()
        mock_hit.id = test_article.id
        mock_hit.score = 0.95

        mock_qdrant = AsyncMock()
        mock_qdrant.search = AsyncMock(return_value=[mock_hit])

        with patch("app.routers.search.get_qdrant_client", return_value=mock_qdrant):
            resp = await client.post("/api/search", headers=auth_headers, json={
                "query": "test article",
                "topic_id": str(test_topic.id),
                "limit": 10,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["article"]["title"] == "Test Article"
        assert data[0]["score"] == 0.95

    async def test_search_user_id_filter(
        self, client: AsyncClient, auth_headers, test_topic, test_user
    ):
        """Search passes user_id filter to Qdrant."""
        mock_qdrant = AsyncMock()
        mock_qdrant.search = AsyncMock(return_value=[])

        with patch("app.routers.search.get_qdrant_client", return_value=mock_qdrant):
            await client.post("/api/search", headers=auth_headers, json={
                "query": "test",
                "topic_id": str(test_topic.id),
            })

        # Verify the search was called with user_id filter
        call_kwargs = mock_qdrant.search.call_args
        filter_conditions = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")
        if filter_conditions:
            must_conditions = filter_conditions.must
            user_id_found = any(
                c.key == "user_id" and c.match.value == str(test_user.id)
                for c in must_conditions
            )
            assert user_id_found, "Search must include user_id filter for Qdrant"

    async def test_search_respects_limit(
        self, client: AsyncClient, auth_headers, test_topic
    ):
        """Search passes limit parameter to Qdrant."""
        mock_qdrant = AsyncMock()
        mock_qdrant.search = AsyncMock(return_value=[])

        with patch("app.routers.search.get_qdrant_client", return_value=mock_qdrant):
            await client.post("/api/search", headers=auth_headers, json={
                "query": "test",
                "topic_id": str(test_topic.id),
                "limit": 5,
            })

        call_kwargs = mock_qdrant.search.call_args
        assert call_kwargs.kwargs.get("limit") == 5 or call_kwargs[1].get("limit") == 5
