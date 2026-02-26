import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db, get_current_user
from app.models import User, ApiKey, RefreshToken

router = APIRouter()
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

ACCESS_TOKEN_EXPIRE = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE = timedelta(days=30)


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        """Validate password meets minimum strength requirements."""
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


def _create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + ACCESS_TOKEN_EXPIRE,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def _create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Email already registered")

    user = User(
        email=req.email,
        display_name=req.display_name,
        password_hash=ph.hash(req.password),
    )
    db.add(user)

    # flush() can raise IntegrityError if a concurrent request registered
    # the same email between our SELECT check and this INSERT. Handle it
    # gracefully instead of letting it bubble as HTTP 500.
    try:
        await db.flush()
    except Exception as e:
        # Check for unique constraint violation (asyncpg UniqueViolationError)
        error_str = str(e).lower()
        if "unique" in error_str or "duplicate" in error_str or "23505" in error_str:
            raise HTTPException(409, "Email already registered")
        raise

    refresh_raw = _create_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hashlib.sha256(refresh_raw.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRE,
    )
    db.add(rt)

    return TokenResponse(
        access_token=_create_access_token(str(user.id)),
        refresh_token=refresh_raw,
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "Invalid credentials")

    try:
        ph.verify(user.password_hash, req.password)
    except VerifyMismatchError:
        raise HTTPException(401, "Invalid credentials")

    if ph.check_needs_rehash(user.password_hash):
        user.password_hash = ph.hash(req.password)

    user.last_login_at = datetime.now(timezone.utc)

    refresh_raw = _create_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hashlib.sha256(refresh_raw.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRE,
    )
    db.add(rt)

    # Cap active (unexpired) refresh tokens per user at 10. Without this, each login
    # creates a new token indefinitely (multiple devices, page refreshes).
    # Delete oldest tokens beyond the cap to prevent unbounded accumulation.
    # IMPORTANT: Only count unexpired tokens — expired tokens are functionally dead
    # (rejected by /auth/refresh) and cleaned up by the daily cleanup task.
    # Counting all tokens would prematurely trigger the cap when expired tokens
    # accumulate, potentially deleting the user's only active session.
    from sqlalchemy import func as sa_func
    token_count_result = await db.execute(
        select(sa_func.count(RefreshToken.id)).where(
            RefreshToken.user_id == user.id,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    active_count = token_count_result.scalar()
    if active_count > 10:
        oldest_tokens = await db.execute(
            select(RefreshToken.id).where(
                RefreshToken.user_id == user.id,
                RefreshToken.expires_at > datetime.now(timezone.utc),
            ).order_by(RefreshToken.created_at.asc()).limit(
                active_count - 10
            )
        )
        old_ids = [row[0] for row in oldest_tokens.all()]
        if old_ids:
            await db.execute(
                RefreshToken.__table__.delete().where(RefreshToken.id.in_(old_ids))
            )

    return TokenResponse(
        access_token=_create_access_token(str(user.id)),
        refresh_token=refresh_raw,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hashlib.sha256(req.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    rt = result.scalar_one_or_none()
    if not rt:
        raise HTTPException(401, "Invalid or expired refresh token")

    user = await db.get(User, rt.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User inactive")

    # Rotate: delete old, issue new
    await db.delete(rt)

    new_refresh_raw = _create_refresh_token()
    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=hashlib.sha256(new_refresh_raw.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRE,
    )
    db.add(new_rt)

    return TokenResponse(
        access_token=_create_access_token(str(user.id)),
        refresh_token=new_refresh_raw,
    )


@router.post("/logout")
async def logout(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Invalidate a refresh token on explicit logout.

    Accepts the refresh token and deletes it from the database,
    preventing it from being used to generate new access tokens.
    The current access token will expire naturally (15 min).
    """
    token_hash = hashlib.sha256(req.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()
    if rt:
        await db.delete(rt)
    # Always return 200 — don't reveal whether the token existed
    return {"status": "logged_out"}
