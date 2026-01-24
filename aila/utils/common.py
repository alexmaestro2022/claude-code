"""
Common utilities for the AILA project.

Provides:
- retry_async: Retry decorator with exponential backoff
- TTLCache: In-memory cache with TTL
- RateLimiter: Async rate limiter
- AsyncHTTPClient: Shared aiohttp session management
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_async(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Retry decorator with exponential backoff for async functions."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_attempts} "
                            f"failed: {e}. Retrying in {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


class TTLCache:
    """Simple in-memory cache with TTL (time-to-live)."""

    __slots__ = ("_cache", "_default_ttl")

    def __init__(self, default_ttl: float = 60.0):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str, ttl: Optional[float] = None) -> Optional[Any]:
        """Get cached value if not expired."""
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.time() - timestamp < (ttl or self._default_ttl):
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        """Set cached value with current timestamp."""
        self._cache[key] = (value, time.time())

    def invalidate(self, key: Optional[str] = None) -> None:
        """Invalidate specific key or all cache."""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    def cleanup(self, ttl: Optional[float] = None) -> None:
        """Remove expired entries."""
        now = time.time()
        max_age = ttl or self._default_ttl
        expired = [k for k, (_, ts) in self._cache.items() if now - ts >= max_age]
        for k in expired:
            del self._cache[k]


class RateLimiter:
    """Async rate limiter with sliding window."""

    __slots__ = ("_max_requests", "_window_seconds", "_timestamps", "_lock")

    def __init__(self, max_requests: int = 10, window_seconds: float = 1.0):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request slot is available."""
        async with self._lock:
            now = time.time()
            # Remove timestamps outside window
            self._timestamps = [
                ts for ts in self._timestamps
                if now - ts < self._window_seconds
            ]
            if len(self._timestamps) >= self._max_requests:
                sleep_time = self._timestamps[0] + self._window_seconds - now
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            self._timestamps.append(time.time())


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value between min and max."""
    return max(min_val, min(max_val, value))
