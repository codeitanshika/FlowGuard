"""Shared helpers for tests/integration/ and tests/chaos/ — both drive the
real, running docker-compose stack over HTTP, never mocks. Base URLs
default to the same localhost ports docs/deployment/setup-guide.md uses
throughout, overridable via env for CI or a non-default compose setup."""

import os
import time
import uuid
from typing import Any

import httpx

GATEWAY_URL = os.environ.get("FLOWGUARD_GATEWAY_URL", "http://localhost:8000")
PAYMENT_URL = os.environ.get("FLOWGUARD_PAYMENT_URL", "http://localhost:8001")
FRAUD_URL = os.environ.get("FLOWGUARD_FRAUD_URL", "http://localhost:8002")
USER_URL = os.environ.get("FLOWGUARD_USER_URL", "http://localhost:8003")
NOTIFICATION_URL = os.environ.get("FLOWGUARD_NOTIFICATION_URL", "http://localhost:8004")
MONITOR_URL = os.environ.get("FLOWGUARD_MONITOR_URL", "http://localhost:8005")
OPS_CONTROLLER_URL = os.environ.get("FLOWGUARD_OPS_CONTROLLER_URL", "http://127.0.0.1:8006")
HEALER_URL = os.environ.get("FLOWGUARD_HEALER_URL", "http://localhost:8007")
FRAUD_AGENT_URL = os.environ.get("FLOWGUARD_FRAUD_AGENT_URL", "http://localhost:8008")

# Local-dev-only values, the same ones documented in .env.example — never
# real credentials. Overridable so a differently-configured stack still works.
MERCHANT_CLIENT_ID = os.environ.get("FLOWGUARD_TEST_CLIENT_ID", "test-merchant")
MERCHANT_CLIENT_SECRET = os.environ.get("FLOWGUARD_TEST_CLIENT_SECRET", "test-secret-123")
READONLY_CLIENT_ID = os.environ.get("FLOWGUARD_READONLY_CLIENT_ID", "readonly-client")
READONLY_CLIENT_SECRET = os.environ.get("FLOWGUARD_READONLY_CLIENT_SECRET", "readonly-secret-123")
OPS_HEALER_TOKEN = os.environ.get("OPS_HEALER_TOKEN", "local-dev-healer-token-change-me")

_HEALTH_ENDPOINTS = [
    f"{GATEWAY_URL}/health",
    f"{PAYMENT_URL}/health",
    f"{FRAUD_URL}/health",
    f"{USER_URL}/health",
    f"{MONITOR_URL}/health",
    f"{HEALER_URL}/health",
    f"{FRAUD_AGENT_URL}/health",
]


class StackUnavailable(Exception):
    pass


def check_stack_is_up(timeout: float = 1.5) -> None:
    """Raises StackUnavailable with a clear, actionable message if the
    docker-compose stack isn't reachable — these tests are black-box
    against real running services, not something a bare `pytest` should
    silently try (and slowly fail) to run."""

    unreachable = []
    for url in _HEALTH_ENDPOINTS:
        try:
            response = httpx.get(url, timeout=timeout)
            if response.status_code != 200:
                unreachable.append(f"{url} -> {response.status_code}")
        except httpx.HTTPError as exc:
            unreachable.append(f"{url} -> {exc}")
    if unreachable:
        raise StackUnavailable(
            "the FlowGuard stack isn't fully up — run `docker compose up --build -d` "
            "from the repo root first. Unreachable:\n  " + "\n  ".join(unreachable)
        )


def login(client_id: str = MERCHANT_CLIENT_ID, client_secret: str = MERCHANT_CLIENT_SECRET) -> str:
    response = httpx.post(
        f"{GATEWAY_URL}/auth/login",
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["data"]["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_user(token: str, email: str | None = None) -> str:
    email = email or f"test-{uuid.uuid4().hex[:12]}@example.com"
    response = httpx.post(
        f"{GATEWAY_URL}/api/v1/users",
        headers=auth_headers(token),
        json={"email": email, "full_name": "Test User", "currency": "USD"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["data"]["id"]


def credit_user(user_id: str, amount: str = "1000.00") -> None:
    # Direct to User Service's internal endpoint — the same route every
    # earlier phase's manual testing used to fund a test account; there is
    # no public deposit endpoint (see docs/PROJECT_OVERVIEW.md).
    response = httpx.post(
        f"{USER_URL}/internal/users/{user_id}/credit",
        json={"amount": amount, "currency": "USD", "transaction_id": str(uuid.uuid4())},
        timeout=10.0,
    )
    response.raise_for_status()


def create_funded_user(token: str, balance: str = "1000.00") -> str:
    user_id = create_user(token)
    credit_user(user_id, balance)
    return user_id


def make_payment(token: str, user_id: str, amount: str = "1.00", idempotency_key: str | None = None) -> httpx.Response:
    idempotency_key = idempotency_key or f"test-{uuid.uuid4().hex}"
    return httpx.post(
        f"{GATEWAY_URL}/api/v1/payments",
        headers={**auth_headers(token), "Idempotency-Key": idempotency_key},
        json={"user_id": user_id, "amount": amount, "currency": "USD"},
        timeout=15.0,
    )


def get_user(token: str, user_id: str) -> dict[str, Any]:
    response = httpx.get(f"{GATEWAY_URL}/api/v1/users/{user_id}", headers=auth_headers(token), timeout=10.0)
    response.raise_for_status()
    return response.json()["data"]


def enable_fault(token: str, target: str, mode: str, error_rate: float = 1.0, latency_ms: int = 0, duration_seconds: int = 30) -> None:
    response = httpx.post(
        f"{GATEWAY_URL}/api/v1/debug/fault-inject",
        headers=auth_headers(token),
        json={
            "target": target, "mode": mode, "error_rate": error_rate,
            "latency_ms": latency_ms, "duration_seconds": duration_seconds,
        },
        timeout=10.0,
    )
    response.raise_for_status()


def clear_fault(token: str, target: str) -> None:
    response = httpx.delete(
        f"{GATEWAY_URL}/api/v1/debug/fault-inject", params={"target": target}, headers=auth_headers(token), timeout=10.0
    )
    response.raise_for_status()


def ops_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {OPS_HEALER_TOKEN}"}


def get_breaker_states() -> dict[str, str]:
    response = httpx.get(f"{OPS_CONTROLLER_URL}/ops/circuits", headers=ops_headers(), timeout=10.0)
    response.raise_for_status()
    return response.json()["data"]


def poll_until(predicate, timeout_seconds: float, interval_seconds: float = 2.0) -> bool:
    """Polls `predicate()` (a zero-arg callable returning bool) until it
    returns True or the timeout elapses. Returns whether it succeeded —
    callers decide whether that's an assertion failure or a measurement."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_seconds)
    return predicate()  # one last check right at the deadline
