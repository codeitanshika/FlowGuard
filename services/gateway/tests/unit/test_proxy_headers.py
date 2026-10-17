"""Verifies GATEWAY_TRUST_PROXY_HEADERS (ADR-0020): fronting the Gateway
with a reverse proxy (Caddy, in production) means every request arrives
from the proxy's own container IP unless the Gateway is told to trust
X-Forwarded-For — and the login rate limiter (app/api/auth.py) keys
per caller IP, so without this fix every real caller behind the proxy
would share one rate-limit bucket. Needs a real Redis (the rate limiter
connects to one at startup); run:
    docker run -d --rm -p 16379:6379 redis:7.4-alpine
    REDIS_URL=redis://localhost:16379/0 python -m pytest tests/unit/test_proxy_headers.py -v
"""

import importlib
import os
import sys
import uuid

import pytest
import redis.asyncio as redis
from starlette.testclient import TestClient

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:16379/0")

BASE_ENV = {
    "GATEWAY_JWT_SECRET": "test-secret-at-least-this-long",
    "GATEWAY_CLIENTS": "{}",
    "GATEWAY_PAYMENT_SERVICE_URL": "http://unused.invalid",
    "GATEWAY_USER_SERVICE_URL": "http://unused.invalid",
    "GATEWAY_NOTIFICATION_SERVICE_URL": "http://unused.invalid",
    "GATEWAY_FRAUD_SERVICE_URL": "http://unused.invalid",
    "GATEWAY_LOGIN_RATE_LIMIT_PER_MINUTE": "2",
    "REDIS_URL": REDIS_URL,
}


@pytest.fixture(autouse=True)
async def _require_redis():
    client = redis.from_url(REDIS_URL)
    try:
        await client.ping()
    except redis.RedisError as exc:
        pytest.skip(f"no Redis at {REDIS_URL} — see this file's module docstring ({exc})")
    finally:
        await client.aclose()


def _fresh_gateway_app(trust_proxy_headers: bool):
    """Re-imports app.main under a distinct env so each test gets its own
    lru_cache'd Settings and its own rate-limit window — `app.*` modules
    are purged from sys.modules first since get_settings() is cached at
    first import and FastAPI's `app` object is built once at module
    scope."""
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    env = {**BASE_ENV, "GATEWAY_TRUST_PROXY_HEADERS": "true" if trust_proxy_headers else "false"}
    old_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        main = importlib.import_module("app.main")
        return main.app
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _attempt_login(client: TestClient, forwarded_for: str) -> int:
    response = client.post(
        "/auth/login",
        json={"client_id": "nonexistent", "client_secret": "wrong"},
        headers={"X-Forwarded-For": forwarded_for},
    )
    return response.status_code


async def _clear_rate_limit_keys():
    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        async for key in client.scan_iter("ratelimit:*"):
            await client.delete(key)
    finally:
        await client.aclose()


async def test_without_trust_every_forwarded_ip_shares_one_bucket():
    await _clear_rate_limit_keys()
    app = _fresh_gateway_app(trust_proxy_headers=False)

    with TestClient(app) as client:
        # Two attempts from "different" forwarded IPs — but since the
        # setting is off, both are seen as the one TestClient transport
        # peer, so they share the same 2/min budget.
        assert _attempt_login(client, f"203.0.113.{uuid.uuid4().int % 250}") == 401
        assert _attempt_login(client, f"203.0.113.{uuid.uuid4().int % 250}") == 401
        exhausted = _attempt_login(client, f"203.0.113.{uuid.uuid4().int % 250}")
    assert exhausted == 429, "expected the shared bucket to be exhausted by the third attempt"


async def test_with_trust_each_forwarded_ip_gets_its_own_bucket():
    await _clear_rate_limit_keys()
    app = _fresh_gateway_app(trust_proxy_headers=True)

    first_ip = f"203.0.113.{uuid.uuid4().int % 250}"
    second_ip = f"203.0.113.{(uuid.uuid4().int % 250) + 1}"

    with TestClient(app) as client:
        assert _attempt_login(client, first_ip) == 401
        assert _attempt_login(client, first_ip) == 401
        # third_ip's own real caller just arrived for the first time — must
        # not be penalized by first_ip's exhausted budget.
        assert _attempt_login(client, second_ip) == 401
        first_ip_exhausted = _attempt_login(client, first_ip)
    assert first_ip_exhausted == 429, "first_ip's own third attempt should still be rate-limited"
