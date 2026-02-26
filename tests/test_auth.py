"""Integration tests for authentication flow: register, login, refresh, logout."""
import hashlib
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import TEST_EMAIL, TEST_PASSWORD


@pytest.mark.asyncio
class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        """Register a new user and receive tokens."""
        resp = await client.post("/auth/register", json={
            "email": "newuser@example.com",
            "display_name": "New User",
            "password": "StrongPass1!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_register_weak_password(self, client: AsyncClient):
        """Reject passwords that don't meet requirements."""
        resp = await client.post("/auth/register", json={
            "email": "weak@example.com",
            "display_name": "Weak Pass",
            "password": "short",
        })
        assert resp.status_code == 422  # validation error

    async def test_register_duplicate_email(self, client: AsyncClient, test_user):
        """Reject registration with existing email."""
        resp = await client.post("/auth/register", json={
            "email": TEST_EMAIL,
            "display_name": "Duplicate",
            "password": "StrongPass1!",
        })
        assert resp.status_code == 409

    async def test_register_invalid_email(self, client: AsyncClient):
        """Reject invalid email format."""
        resp = await client.post("/auth/register", json={
            "email": "not-an-email",
            "display_name": "Invalid",
            "password": "StrongPass1!",
        })
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestLogin:
    async def test_login_success(self, client: AsyncClient, test_user):
        """Login with valid credentials."""
        resp = await client.post("/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_login_wrong_password(self, client: AsyncClient, test_user):
        """Reject login with wrong password."""
        resp = await client.post("/auth/login", json={
            "email": TEST_EMAIL,
            "password": "WrongPassword1!",
        })
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Reject login for user that doesn't exist."""
        resp = await client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "Whatever123!",
        })
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestRefresh:
    async def test_refresh_success(self, client: AsyncClient, test_user, db_session):
        """Refresh token grants a new access token and rotates refresh token."""
        from app.models import RefreshToken
        import secrets

        raw_token = secrets.token_urlsafe(48)
        rt = RefreshToken(
            user_id=test_user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add(rt)
        await db_session.flush()

        resp = await client.post("/auth/refresh", json={
            "refresh_token": raw_token,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New refresh token should differ from old one (rotation)
        assert data["refresh_token"] != raw_token

    async def test_refresh_expired_token(self, client: AsyncClient, test_user, db_session):
        """Reject expired refresh token."""
        from app.models import RefreshToken
        import secrets

        raw_token = secrets.token_urlsafe(48)
        rt = RefreshToken(
            user_id=test_user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # expired
        )
        db_session.add(rt)
        await db_session.flush()

        resp = await client.post("/auth/refresh", json={
            "refresh_token": raw_token,
        })
        assert resp.status_code == 401

    async def test_refresh_invalid_token(self, client: AsyncClient):
        """Reject completely invalid refresh token."""
        resp = await client.post("/auth/refresh", json={
            "refresh_token": "totally-bogus-token",
        })
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestLogout:
    async def test_logout_success(self, client: AsyncClient, test_user, db_session):
        """Logout invalidates the refresh token."""
        from app.models import RefreshToken
        import secrets

        raw_token = secrets.token_urlsafe(48)
        rt = RefreshToken(
            user_id=test_user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add(rt)
        await db_session.flush()

        resp = await client.post("/auth/logout", json={
            "refresh_token": raw_token,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "logged_out"

        # Token should no longer work for refresh
        resp2 = await client.post("/auth/refresh", json={
            "refresh_token": raw_token,
        })
        assert resp2.status_code == 401

    async def test_logout_nonexistent_token(self, client: AsyncClient):
        """Logout with unknown token still returns 200 (no information leak)."""
        resp = await client.post("/auth/logout", json={
            "refresh_token": "nonexistent-token",
        })
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestProtectedEndpoints:
    async def test_unauthenticated_request(self, client: AsyncClient):
        """Request without auth token returns 401."""
        resp = await client.get("/api/topics")
        assert resp.status_code in (401, 403)

    async def test_invalid_jwt(self, client: AsyncClient):
        """Request with malformed JWT returns 401."""
        resp = await client.get("/api/topics", headers={
            "Authorization": "Bearer invalid.jwt.token",
        })
        assert resp.status_code == 401

    async def test_expired_jwt(self, client: AsyncClient, test_user):
        """Request with expired JWT returns 401."""
        import jwt

        token = jwt.encode(
            {
                "sub": str(test_user.id),
                "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
                "iat": datetime.now(timezone.utc) - timedelta(minutes=20),
            },
            "test-secret-key-for-testing-only",
            algorithm="HS256",
        )
        resp = await client.get("/api/topics", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 401
