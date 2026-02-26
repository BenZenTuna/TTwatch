import os
import hashlib
import uuid
from datetime import datetime, timezone

import jwt
import redis.asyncio as aioredis
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from fastapi import Depends, HTTPException, Security, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings
from app.models import User, ApiKey

# === Database Engine ===
engine = create_async_engine(
    settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://"),
    pool_size=20,
    max_overflow=10,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI dependency that yields a DB session per request."""
    async with async_session() as session:
        async with session.begin():
            yield session


# === Redis Connections ===
dedup_redis = aioredis.from_url(
    os.environ.get("REDIS_DEDUP_URL", "redis://redis:6379/2")
)
cache_redis = aioredis.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

# === Rate Limiter ===
from app.middleware.rate_limit import RateLimiter

rate_limiter = RateLimiter(
    redis_url=os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

# === Auth ===
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve user from JWT token OR API key. Exactly one must be present.
    Also sets the RLS context variable for row-level security policies."""

    user: User | None = None

    if credentials and credentials.credentials:
        try:
            payload = jwt.decode(
                credentials.credentials, settings.JWT_SECRET, algorithms=["HS256"]
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Invalid token")

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Invalid token payload")
        try:
            user_id = uuid.UUID(user_id)
        except (ValueError, AttributeError):
            raise HTTPException(401, "Invalid token payload")
        user = await db.get(User, user_id)
        if not user or not user.is_active:
            raise HTTPException(401, "User not found or inactive")

    elif api_key:
        prefix = api_key[:14]
        candidates = await db.execute(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.is_active == True,
            )
        )
        for candidate in candidates.scalars():
            if hashlib.sha256(api_key.encode()).hexdigest() == candidate.key_hash:
                candidate.last_used_at = datetime.now(timezone.utc)
                user = await db.get(User, candidate.user_id)
                if not user or not user.is_active:
                    raise HTTPException(401, "User inactive")
                break
        if not user:
            raise HTTPException(401, "Invalid API key")

    else:
        raise HTTPException(401, "Authentication required")

    # Set RLS context for row-level security policies.
    # Uses validated UUID (only [0-9a-f-] chars) so f-string is safe here.
    validated_id = str(uuid.UUID(str(user.id)))
    await db.execute(text(
        f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
    ))
    return user


async def set_rls_context(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set PostgreSQL RLS context for this request.

    Uses f-string formatting, which is safe here because the UUID is
    validated via round-trip: str(uuid.UUID(...)) guarantees only
    [0-9a-f-] characters. PostgreSQL SET does not accept bind parameters.
    """
    validated_id = str(uuid.UUID(str(user.id)))
    await db.execute(text(
        f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
    ))
    return user


async def rate_limit_dependency(
    user: User = Depends(get_current_user),
    request: Request = None,
):
    """Apply rate limiting per user per endpoint."""
    endpoint = request.url.path if request else "unknown"
    await rate_limiter.check(str(user.id), endpoint)
    return user
