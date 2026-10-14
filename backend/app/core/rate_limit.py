import logging
import time
from collections import defaultdict

import redis

logger = logging.getLogger(__name__)


class MemoryRateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = [stamp for stamp in self._hits[key] if now - stamp < 60]
        if len(window) >= self.limit:
            self._hits[key] = window
            return False
        window.append(now)
        self._hits[key] = window
        return True


class RedisRateLimiter:
    def __init__(self, client: redis.Redis, limit_per_minute: int):
        self.client = client
        self.limit = limit_per_minute

    def allow(self, key: str) -> bool:
        redis_key = f"rl:{key}"
        count = self.client.incr(redis_key)
        if count == 1:
            self.client.expire(redis_key, 60)
        return int(count) <= self.limit


class FallbackRateLimiter:
    def __init__(self, primary: RedisRateLimiter, fallback: MemoryRateLimiter):
        self.primary = primary
        self.fallback = fallback

    def allow(self, key: str) -> bool:
        try:
            return self.primary.allow(key)
        except redis.RedisError:
            logger.warning("redis rate limiter unavailable; using process memory fallback")
            return self.fallback.allow(key)


def build_rate_limiter(redis_url: str, limit_per_minute: int):
    memory = MemoryRateLimiter(limit_per_minute)
    if not redis_url:
        return memory
    client = redis.Redis.from_url(redis_url, socket_timeout=1, socket_connect_timeout=1)
    return FallbackRateLimiter(RedisRateLimiter(client, limit_per_minute), memory)
