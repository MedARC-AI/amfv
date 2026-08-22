from __future__ import annotations

import hashlib
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import settings


@dataclass
class _Bucket:
    count: int
    reset_at: float


class InMemoryRateLimiter:
    def __init__(self, max_buckets: int | None = None) -> None:
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._max_buckets = max_buckets
        self._lock = Lock()

    def hit(
        self, key: str, *, limit: int, window_seconds: int, increment: bool = True
    ) -> int | None:
        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            bucket = self._buckets.get(key)
            if bucket is None or bucket.reset_at <= now:
                bucket = _Bucket(count=0, reset_at=now + window_seconds)
                self._buckets[key] = bucket
                self._trim_to_limit()
            else:
                self._buckets.move_to_end(key)

            if bucket.count >= limit:
                return max(1, math.ceil(bucket.reset_at - now))

            if increment:
                bucket.count += 1

            return None

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def bucket_count(self) -> int:
        with self._lock:
            self._prune_expired(time.monotonic())
            return len(self._buckets)

    def _prune_expired(self, now: float) -> None:
        expired_keys = [
            key for key, bucket in self._buckets.items() if bucket.reset_at <= now
        ]
        for key in expired_keys:
            self._buckets.pop(key, None)

    def _trim_to_limit(self) -> None:
        max_buckets = self._max_buckets or settings.AUTH_RATE_LIMIT_MAX_BUCKETS
        while len(self._buckets) > max_buckets:
            self._buckets.popitem(last=False)


rate_limiter = InMemoryRateLimiter()


def check_rate_limit(
    request: Request,
    *,
    scope: str,
    limit: int,
    identity: str | None = None,
    window_seconds: int | None = None,
    increment: bool = True,
) -> None:
    if not settings.AUTH_RATE_LIMIT_ENABLED:
        return

    window = window_seconds or settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
    key = _bucket_key(scope, identity or client_identifier(request))
    retry_after = rate_limiter.hit(
        key, limit=limit, window_seconds=window, increment=increment
    )
    if retry_after is None:
        return

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def client_scoped_identity(request: Request, identity: str) -> str:
    return (
        f"{_identity_digest(client_identifier(request))}:{_identity_digest(identity)}"
    )


def client_identifier(request: Request) -> str:
    peer = request.client.host.lower() if request.client and request.client.host else ""
    trusted_proxies = _csv_setting(settings.AUTH_RATE_LIMIT_TRUSTED_PROXY_IPS)
    trusted_header = settings.AUTH_RATE_LIMIT_CLIENT_IP_HEADER.strip().lower()
    if peer in trusted_proxies and trusted_header:
        value = request.headers.get(trusted_header)
        if value:
            return value.split(",", 1)[0].strip().lower()
    if peer:
        return peer
    return "unknown"


def _bucket_key(scope: str, identity: str) -> str:
    normalized = identity.strip().lower() or "unknown"
    normalized = normalized[: settings.AUTH_RATE_LIMIT_IDENTITY_MAX_LENGTH]
    return f"{scope}:{_identity_digest(normalized)}"


def _csv_setting(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _identity_digest(identity: str) -> str:
    return hashlib.sha256(identity.strip().lower().encode()).hexdigest()
