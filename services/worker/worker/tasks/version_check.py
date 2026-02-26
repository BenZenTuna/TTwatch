"""Periodic task: check upstream service versions and cache results in Redis."""
import json
import logging
import os

import redis

from worker.celeryconfig import app
from app.services.version_checker import REDIS_KEY, CACHE_TTL, check_all_versions

logger = logging.getLogger(__name__)

_cache_redis = redis.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)


@app.task(name="check_service_versions")
def check_service_versions():
    """Beat task: check all tracked services for new versions and cache results."""
    result = check_all_versions()
    _cache_redis.set(REDIS_KEY, json.dumps(result), ex=CACHE_TTL)
    updates = sum(1 for s in result["services"] if s["has_update"])
    logger.info(
        f"check_service_versions: checked {len(result['services'])} services, "
        f"{updates} updates available"
    )
    return {"status": "ok", "updates_available": updates}
