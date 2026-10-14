from app.core.rate_limit import MemoryRateLimiter


def test_memory_rate_limiter_blocks_after_the_limit():
    limiter = MemoryRateLimiter(2)
    assert limiter.allow("client") is True
    assert limiter.allow("client") is True
    assert limiter.allow("client") is False
    assert limiter.allow("other") is True
