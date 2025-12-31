"""
Unit tests for rate limiting.
"""

import time

import pytest

from src.api.rate_limit import RateLimiter, TokenBucket


class TestTokenBucket:
    """Tests for TokenBucket."""

    def test_consume_success(self):
        """Test successful token consumption."""
        bucket = TokenBucket(
            capacity=10,
            tokens=10,
            refill_rate=1.0,
            last_refill=time.time(),
        )

        assert bucket.consume(1) is True
        assert bucket.tokens == 9

    def test_consume_empty_bucket(self):
        """Test consumption from empty bucket."""
        bucket = TokenBucket(
            capacity=10,
            tokens=0,
            refill_rate=1.0,
            last_refill=time.time(),
        )

        assert bucket.consume(1) is False

    def test_refill_over_time(self):
        """Test token refill based on time."""
        bucket = TokenBucket(
            capacity=10,
            tokens=0,
            refill_rate=10.0,  # 10 tokens per second
            last_refill=time.time() - 1,  # 1 second ago
        )

        # Should refill 10 tokens
        assert bucket.consume(5) is True
        assert bucket.tokens >= 5  # Allow for some timing variance

    def test_capacity_limit(self):
        """Test that tokens don't exceed capacity."""
        bucket = TokenBucket(
            capacity=10,
            tokens=10,
            refill_rate=100.0,
            last_refill=time.time() - 10,  # Long time ago
        )

        # Trigger refill
        bucket.consume(1)

        # Should be capped at capacity
        assert bucket.tokens <= 10

    def test_wait_time(self):
        """Test wait time calculation."""
        bucket = TokenBucket(
            capacity=10,
            tokens=0.5,
            refill_rate=1.0,
            last_refill=time.time(),
        )

        # Need 0.5 more tokens at 1/second = 0.5 seconds
        assert bucket.wait_time == pytest.approx(0.5, rel=0.1)


class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_check_allowed(self):
        """Test request is allowed when under limit."""
        limiter = RateLimiter(requests_per_minute=60)

        allowed, wait_time = limiter.check("tenant-1")

        assert allowed is True
        assert wait_time == 0

    def test_check_rate_limited(self):
        """Test request is blocked when over limit."""
        limiter = RateLimiter(requests_per_minute=1, burst_capacity=1)

        # First request succeeds
        allowed1, _ = limiter.check("tenant-1")
        assert allowed1 is True

        # Second request is blocked
        allowed2, wait_time = limiter.check("tenant-1")
        assert allowed2 is False
        assert wait_time > 0

    def test_per_tenant_limits(self):
        """Test that rate limits are per-tenant."""
        limiter = RateLimiter(requests_per_minute=1, burst_capacity=1)

        # Tenant 1 uses their limit
        limiter.check("tenant-1")

        # Tenant 2 should still be allowed
        allowed, _ = limiter.check("tenant-2")
        assert allowed is True

    def test_reset(self):
        """Test resetting rate limit for a key."""
        limiter = RateLimiter(requests_per_minute=1, burst_capacity=1)

        # Use up the limit
        limiter.check("tenant-1")
        allowed, _ = limiter.check("tenant-1")
        assert allowed is False

        # Reset
        limiter.reset("tenant-1")

        # Should be allowed again
        allowed, _ = limiter.check("tenant-1")
        assert allowed is True
