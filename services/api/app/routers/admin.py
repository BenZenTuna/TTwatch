"""Admin-only endpoints — service version management."""
from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user, cache_redis
from app.models import User
from app.services.version_checker import check_and_cache_versions, get_cached_versions

router = APIRouter()


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency that ensures the current user is an admin."""
    if not user.is_admin:
        raise HTTPException(403, "Admin access required")
    return user


@router.get("/versions")
async def get_version_status(user: User = Depends(require_admin)):
    """Return cached service version status."""
    result = await get_cached_versions(cache_redis)
    if not result:
        return {"checked_at": None, "services": []}
    return result


@router.post("/versions/check")
async def trigger_version_check(user: User = Depends(require_admin)):
    """Trigger a fresh version check against upstream registries."""
    result = await check_and_cache_versions(cache_redis)
    return result
