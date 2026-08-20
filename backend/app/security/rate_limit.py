"""Per-user rate limiting backed by Redis.

Redis is mandatory, not optional (DECISIONS.md): the API is stateless and scales
horizontally, so a per-replica in-memory counter would let a user who hits the limit
on replica A simply get routed to replica B — doubling their effective limit for
every replica added. The counter has to be shared or it isn't a limit.

Uses a fixed-window counter via INCR + EXPIRE, executed as one atomic Lua script so
a crash between INCR and EXPIRE can't leave a key without a TTL (which would lock a
user out permanently).
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_INCR_WITH_TTL = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RateLimiter:
    def __init__(self, redis: Redis, *, fail_open: bool = False) -> None:
        """Defaults closed: if Redis is unreachable, requests are REJECTED rather
        than let through. Rate limiting exists specifically to survive a dependency
        outage without one client exhausting capacity/budget for everyone — failing
        open during exactly that outage defeats the purpose. Availability during a
        Redis outage is a deliberate choice to make per-deployment, not a default."""

        self._redis = redis
        self._script = redis.register_script(_INCR_WITH_TTL)
        self._fail_open = fail_open

    async def check(self, *, key: str, limit: int, window_seconds: int = 60) -> bool:
        """Returns True if the request is allowed. On Redis failure, fail_open
        decides behavior: availability (True) vs strict enforcement (False)."""

        try:
            count = await self._script(keys=[f"ratelimit:{key}"], args=[window_seconds])
        except Exception as exc:
            logger.error("rate limit check failed against Redis: %s", exc)
            return self._fail_open

        return int(count) <= limit
