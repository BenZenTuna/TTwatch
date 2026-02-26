"""User profile and API key management."""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, ApiKey

router = APIRouter()


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    max_topics: int
    max_articles_per_topic: int
    max_api_keys: int
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    display_name: str


class ApiKeyCreate(BaseModel):
    label: str = "default"


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    key_prefix: str
    label: str
    scopes: list
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime
    expires_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreated(BaseModel):
    """Returned only on creation — includes the full key (shown once)."""
    id: uuid.UUID
    key: str
    key_prefix: str
    label: str


@router.get("/me", response_model=UserResponse)
async def get_profile(user: User = Depends(get_current_user)):
    """Return current user profile."""
    return user


@router.put("/me", response_model=UserResponse)
async def update_profile(
    req: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update display name."""
    user.display_name = req.display_name
    return user


@router.get("/me/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all API keys for the current user."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.is_active == True,
        ).order_by(ApiKey.created_at.desc())
    )
    return result.scalars().all()


@router.post("/me/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    req: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key.

    Format: tw_live_{short_id}_{random32}
    The full key is returned ONCE in the response. Only the SHA-256 hash is stored.
    """
    # Enforce API key limit
    count = await db.execute(
        select(func.count(ApiKey.id)).where(
            ApiKey.user_id == user.id,
            ApiKey.is_active == True,
        )
    )
    if count.scalar() >= user.max_api_keys:
        raise HTTPException(403, f"API key limit reached ({user.max_api_keys})")

    # Generate key: tw_live_{short_id}_{random32}
    short_id = str(user.id).replace("-", "")[:4]
    random_part = secrets.token_hex(16)  # 32 hex chars
    full_key = f"tw_live_{short_id}_{random_part}"
    key_prefix = full_key[:14]

    api_key = ApiKey(
        user_id=user.id,
        key_prefix=key_prefix,
        key_hash=hashlib.sha256(full_key.encode()).hexdigest(),
        label=req.label,
    )
    db.add(api_key)
    await db.flush()

    return ApiKeyCreated(
        id=api_key.id,
        key=full_key,
        key_prefix=key_prefix,
        label=req.label,
    )


@router.delete("/me/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (deactivate) an API key."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.user_id == user.id,
        )
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(404, "API key not found")
    api_key.is_active = False
