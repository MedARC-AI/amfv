from fastapi import HTTPException
from starlette.requests import Request

from app.services.rate_limit import InMemoryRateLimiter, check_rate_limit, rate_limiter


def _request(
    *,
    client_host: str = "198.51.100.10",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/login/access-token",
            "headers": headers or [],
            "client": (client_host, 12345),
        }
    )


def test_untrusted_clients_cannot_spoof_rate_limit_ip() -> None:
    rate_limiter.clear()
    try:
        request_one = _request(headers=[(b"cf-connecting-ip", b"203.0.113.1")])
        request_two = _request(headers=[(b"cf-connecting-ip", b"203.0.113.2")])
        request_three = _request(headers=[(b"cf-connecting-ip", b"203.0.113.3")])

        check_rate_limit(request_one, scope="test-spoof", limit=2, window_seconds=60)
        check_rate_limit(request_two, scope="test-spoof", limit=2, window_seconds=60)
        try:
            check_rate_limit(
                request_three,
                scope="test-spoof",
                limit=2,
                window_seconds=60,
            )
        except HTTPException as exc:
            assert exc.status_code == 429
        else:
            raise AssertionError("rate limit did not fire")
    finally:
        rate_limiter.clear()


def test_trusted_proxy_header_can_identify_distinct_clients() -> None:
    rate_limiter.clear()
    try:
        from unittest.mock import patch

        with patch(
            "app.core.config.settings.AUTH_RATE_LIMIT_CLIENT_IP_HEADER",
            "cf-connecting-ip",
        ):
            request_one = _request(
                client_host="127.0.0.1",
                headers=[(b"cf-connecting-ip", b"203.0.113.1")],
            )
            request_two = _request(
                client_host="127.0.0.1",
                headers=[(b"cf-connecting-ip", b"203.0.113.2")],
            )

            check_rate_limit(request_one, scope="test-trusted-proxy", limit=1)
            check_rate_limit(request_two, scope="test-trusted-proxy", limit=1)
    finally:
        rate_limiter.clear()


def test_in_memory_limiter_prunes_and_caps_buckets() -> None:
    limiter = InMemoryRateLimiter(max_buckets=2)

    limiter.hit("expired", limit=1, window_seconds=-1)
    assert limiter.bucket_count() == 0

    limiter.hit("one", limit=1, window_seconds=60)
    limiter.hit("two", limit=1, window_seconds=60)
    limiter.hit("three", limit=1, window_seconds=60)

    assert limiter.bucket_count() == 2
