"""Shared test fixtures for TTwatch API integration tests.

Uses httpx.AsyncClient with ASGI transport to test the FastAPI app
directly (no network I/O). A fresh SQLite database is created per
test session, and each test runs in its own transaction that is rolled
back after the test.
"""
import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Patch settings before importing app
os.environ.update({
    "DATABASE_URL": "sqlite+aiosqlite:///./test.db",
    "REDIS_URL": "redis://localhost:6379/15",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/15",
    "REDIS_DEDUP_URL": "redis://localhost:6379/15",
    "REDIS_CACHE_URL": "redis://localhost:6379/15",
    "QDRANT_URL": "http://localhost:6333",
    "MINIO_URL": "http://localhost:9000",
    "MINIO_ACCESS_KEY": "test",
    "MINIO_SECRET_KEY": "test",
    "MINIO_BUCKET": "test",
    "JWT_SECRET": "test-secret-key-for-testing-only",
    "CORS_ORIGINS": "http://localhost:3000",
    "LLM_PROVIDER": "cloud",
    "VLLM_URL": "",
    "EMBEDDER_URL": "",
    "SEARXNG_URL": "http://localhost:8080",
})

from app.models.base import Base
from app.models.user import User, RefreshToken
from app.models.intelligence import Topic, Article, Cluster, Entity
from app.models.investment import WatchlistItem, PriceAlert


# === Database fixtures ===

TEST_DB_URL = "sqlite+aiosqlite:///./test.db"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Clean up test database file
    if os.path.exists("./test.db"):
        os.remove("./test.db")


@pytest_asyncio.fixture
async def db_session():
    """Yield a fresh database session per test. Rolls back after each test."""
    async with TestSessionLocal() as session:
        async with session.begin():
            yield session
            await session.rollback()


@pytest_asyncio.fixture
async def override_db(db_session):
    """Override the app's get_db dependency to use our test session."""
    from app.deps import get_db

    async def _override():
        yield db_session

    return _override


# === Mock fixtures for external services ===

@pytest.fixture
def mock_init_services():
    """Mock init_all to prevent real Qdrant/MinIO connections."""
    with patch("app.main.init_all") as m:
        yield m


@pytest.fixture
def mock_llm():
    """Mock LLM provider."""
    llm = AsyncMock()
    llm.close = AsyncMock()
    return llm


@pytest.fixture
def mock_embedder():
    """Mock embedding provider."""
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[[0.1] * 1024])
    embedder.close = AsyncMock()
    return embedder


@pytest.fixture
def mock_rate_limiter():
    """Mock rate limiter to always allow."""
    with patch("app.deps.rate_limiter") as m:
        m.check = AsyncMock(return_value=True)
        yield m


@pytest.fixture
def mock_celery():
    """Mock Celery client."""
    with patch("app.celery_client.celery_app") as m:
        m.send_task = MagicMock()
        yield m


# === App fixture ===

@pytest_asyncio.fixture
async def client(override_db, mock_init_services, mock_llm, mock_embedder, mock_rate_limiter):
    """Create an async test client with all external deps mocked."""
    from app.deps import get_db
    from app.main import app

    app.dependency_overrides[get_db] = override_db

    # Mock lifespan state
    app.state.llm = mock_llm
    app.state.embedder = mock_embedder

    # Patch ws_alert_listener to be a no-op
    with patch("app.main.ws_alert_listener", new_callable=AsyncMock):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

    app.dependency_overrides.clear()


# === User + Auth helpers ===

from argon2 import PasswordHasher

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
TEST_PASSWORD = "TestPass123!"
TEST_EMAIL = "test@example.com"


@pytest_asyncio.fixture
async def test_user(db_session):
    """Create a test user in the database."""
    user = User(
        id=uuid.uuid4(),
        email=TEST_EMAIL,
        display_name="Test User",
        password_hash=ph.hash(TEST_PASSWORD),
        is_active=True,
        is_admin=False,
        max_topics=10,
        max_articles_per_topic=5000,
        max_api_keys=5,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def auth_headers(test_user):
    """Return Authorization headers with a valid JWT for test_user."""
    import jwt

    token = jwt.encode(
        {
            "sub": str(test_user.id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
            "iat": datetime.now(timezone.utc),
        },
        "test-secret-key-for-testing-only",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def test_topic(db_session, test_user):
    """Create a test topic."""
    topic = Topic(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Test Topic",
        icon="test",
        config={"search_terms": ["test query"]},
        refresh_interval_minutes=120,
    )
    db_session.add(topic)
    await db_session.flush()
    return topic


@pytest_asyncio.fixture
async def test_article(db_session, test_user, test_topic):
    """Create a test article."""
    article = Article(
        id=uuid.uuid4(),
        user_id=test_user.id,
        topic_id=test_topic.id,
        url="https://example.com/article-1",
        title="Test Article",
        source_name="Example News",
        summary="A test article summary.",
        sentiment_score=0.5,
        relevance_score=0.8,
        is_duplicate=False,
    )
    db_session.add(article)
    await db_session.flush()
    return article
