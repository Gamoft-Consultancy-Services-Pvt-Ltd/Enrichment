"""
LEAD-54 — SignalSet cache (Epic 6: Lead Scoring Runtime)

A tenant's active SignalSet is read on *every* score but changes only when
onboarding regenerates it. LEAD-54 puts a cache in front of the repository's
get_active_signal_set so the hot path doesn't hit Postgres on every lead.

Shape:
- CacheBackend  : a tiny Protocol (get / set / delete). Two implementations:
    * InMemoryCacheBackend  — dict + monotonic expiry; for tests, demo, and
      single-process runs. No external dependency.
    * RedisCacheBackend     — wraps an async redis client; for production,
      consistent with the Redis already used for sessions (Epic 2).
- SignalSetCache: the scoring-facing object. Knows how to build the key,
  serialise the SignalSet payload to/from JSON, apply a TTL, and — crucially —
  invalidate a tenant when its config is regenerated.

Design choices:
- Cache stores the raw SignalSet *payload dict* (what LEAD-47 keeps in the
  JSONB column), not an ORM record. ORM rows are bound to a session and must
  never outlive it; a plain dict is safe to cache and cheap to serialise.
- get_or_load(tenant_id, loader): cache-aside. On miss it calls the async
  loader (the repository), stores the result, and returns it. A cached *miss*
  is intentionally NOT stored — a tenant with no config yet is rare and we want
  it picked up as soon as onboarding writes one.
- TTL is a safety net, not the invalidation mechanism. The real invalidation is
  explicit: save_signal_set (LEAD-48) should call cache.invalidate(tenant_id)
  so a regenerated config is visible immediately, not after the TTL lapses.
- Keys are namespaced ("scoring:signalset:{tenant_id}") so this cache never
  collides with session or other caches sharing the same Redis.
"""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, Protocol


KEY_PREFIX = "scoring:signalset:"
DEFAULT_TTL_SECONDS = 300  # 5 min safety net; explicit invalidation is primary


# --------------------------------------------------------------------------- #
# Backend protocol + implementations                                          #
# --------------------------------------------------------------------------- #
class CacheBackend(Protocol):
    """Minimal async key/value contract the cache needs."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...


class InMemoryCacheBackend:
    """Process-local cache with per-key expiry. For tests / demo / single proc."""

    def __init__(self) -> None:
        # key -> (expires_at_monotonic, value)
        self._store: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)  # lazily evict expired key
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (time.monotonic() + ttl_seconds, value)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class RedisCacheBackend:
    """Wraps an async redis client (redis.asyncio.Redis). Production backend."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        if value is None:
            return None
        # redis may return bytes depending on decode_responses config
        return value.decode() if isinstance(value, (bytes, bytearray)) else value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)


# --------------------------------------------------------------------------- #
# The scoring-facing cache                                                     #
# --------------------------------------------------------------------------- #
class SignalSetCache:
    """Cache-aside wrapper over a tenant's active SignalSet payload."""

    def __init__(
        self,
        backend: CacheBackend,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._backend = backend
        self._ttl = ttl_seconds

    @staticmethod
    def _key(tenant_id: str) -> str:
        return f"{KEY_PREFIX}{tenant_id}"

    async def get_or_load(
        self,
        tenant_id: str,
        loader: Callable[[], Awaitable[dict[str, Any] | None]],
    ) -> dict[str, Any] | None:
        """Return the tenant's active SignalSet payload, caching on miss.

        `loader` is an async callable returning the payload dict (or None) —
        typically a closure over the repository's get_active_signal_set.
        A None result (no config yet) is NOT cached, so it's picked up as soon
        as onboarding writes one.
        """
        key = self._key(tenant_id)

        cached = await self._backend.get(key)
        if cached is not None:
            return json.loads(cached)

        payload = await loader()
        if payload is not None:
            await self._backend.set(key, json.dumps(payload), self._ttl)
        return payload

    async def invalidate(self, tenant_id: str) -> None:
        """Drop a tenant's cached config — call after save_signal_set (LEAD-48).

        This is the primary freshness mechanism; the TTL is only a backstop.
        """
        await self._backend.delete(self._key(tenant_id))
