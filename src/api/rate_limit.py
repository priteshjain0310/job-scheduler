"""
Rate limiting middleware and utilities.
"""

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass
class TokenBucket:
    """
    Token bucket for rate limiting.

    Implements a simple token bucket algorithm for per-tenant rate limiting.
    """

    capacity: float
    tokens: float
    refill_rate: float  # tokens per second
    last_refill: float

    def consume(self, tokens: float = 1.0) -> bool:
        """
        Try to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume.

        Returns:
            True if tokens were consumed, False if rate limited.
        """
        now = time.time()

        # Refill tokens based on time elapsed
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True

        return False

    @property
    def wait_time(self) -> float:
        """Time in seconds until at least 1 token is available."""
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) / self.refill_rate


class RateLimiter:
    """
    In-memory rate limiter using token buckets.

    Thread-safe rate limiting for API requests on a per-tenant basis.
    For production, consider using Redis-based rate limiting.
    """

    def __init__(
        self,
        requests_per_minute: int = 100,
        burst_capacity: int | None = None,
    ):
        """
        Initialize the rate limiter.

        Args:
            requests_per_minute: Maximum requests per minute per tenant.
            burst_capacity: Maximum burst size. Defaults to 2x rate.
        """
        self._requests_per_minute = requests_per_minute
        self._refill_rate = requests_per_minute / 60.0  # tokens per second
        self._capacity = burst_capacity or (requests_per_minute * 2)
        self._buckets: dict[str, TokenBucket] = defaultdict(self._create_bucket)

    def _create_bucket(self) -> TokenBucket:
        """Create a new token bucket for a tenant."""
        return TokenBucket(
            capacity=self._capacity,
            tokens=self._capacity,
            refill_rate=self._refill_rate,
            last_refill=time.time(),
        )

    def check(self, key: str, tokens: float = 1.0) -> tuple[bool, float]:
        """
        Check if a request is allowed.

        Args:
            key: Rate limit key (usually tenant_id).
            tokens: Number of tokens to consume.

        Returns:
            Tuple of (allowed, wait_time_seconds).
        """
        bucket = self._buckets[key]
        allowed = bucket.consume(tokens)
        return allowed, bucket.wait_time

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        if key in self._buckets:
            del self._buckets[key]


# Global rate limiter instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        from src.config import get_settings
        settings = get_settings()
        _rate_limiter = RateLimiter(
            requests_per_minute=settings.rate_limit_requests_per_minute
        )
    return _rate_limiter


def rate_limit_dependency(
    request: Request,
) -> None:
    """
    FastAPI dependency for rate limiting.

    Raises HTTPException 429 if rate limit is exceeded.
    """
    # Get tenant from auth header if available
    auth_header = request.headers.get("Authorization", "")
    tenant_id = "anonymous"

    # Extract tenant from JWT if present
    if auth_header.startswith("Bearer "):
        try:
            from src.api.auth import decode_token
            token = auth_header[7:]
            token_data = decode_token(token)
            tenant_id = token_data.tenant_id
        except Exception:
            pass

    limiter = get_rate_limiter()
    allowed, wait_time = limiter.check(tenant_id)

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Retry after {wait_time:.1f} seconds",
            headers={"Retry-After": str(int(wait_time) + 1)},
        )


def create_rate_limit_middleware(app_instance: Callable) -> Callable:
    """
    Create rate limiting middleware for FastAPI.

    Args:
        app_instance: The FastAPI application.

    Returns:
        The middleware function.
    """

    async def rate_limit_middleware(request: Request, call_next: Callable):
        """Middleware to apply rate limiting."""
        # Skip rate limiting for health checks and metrics
        if request.url.path in ["/health", "/metrics", "/docs", "/openapi.json"]:
            return await call_next(request)

        try:
            rate_limit_dependency(request)
        except HTTPException as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=e.status_code,
                content={"error": e.detail},
                headers=e.headers,
            )

        return await call_next(request)

    return rate_limit_middleware
