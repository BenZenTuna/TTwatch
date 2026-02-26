import redis.asyncio as aioredis
from fastapi import HTTPException

RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
else
    local ttl = redis.call('TTL', key)
    if ttl == -1 then
        redis.call('EXPIRE', key, window)
    end
end

if current > limit then
    return 0
end
return 1
"""


class RateLimiter:
    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url)
        self._script = self.redis.register_script(RATE_LIMIT_SCRIPT)

    async def check(self, user_id: str, endpoint: str,
                    limit: int = 60, window: int = 60) -> bool:
        key = f"ttwatch:rate:{user_id}:{endpoint}"
        allowed = await self._script(keys=[key], args=[limit, window])
        if not allowed:
            raise HTTPException(429, detail="Rate limit exceeded")
        return True
