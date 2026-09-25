# FlowGuard Library Docs

Supersedes the old `LIBRARY_DOCS.md` (now in
[archive/](archive/LIBRARY_DOCS.md)) — content is largely the same, with
usage patterns corrected to reference the Gateway and the Ops Controller
where relevant.

## FastAPI

**What it is:** An async Python web framework built on Starlette and
Pydantic, used for every service's HTTP API — including the Gateway and
the Ops Controller.

**Why we use it:** Native async support, automatic request/response
validation via Pydantic, built-in OpenAPI docs per service.

**Key usage pattern:** Routes stay thin in `app/api/`, calling into
`app/services/` for logic via `Depends()`-injected clients.

```python
@router.post("/payments", response_model=PaymentResponse)
async def create_payment(
    payload: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    payments: PaymentOrchestrator = Depends(get_payment_orchestrator),
):
    return await payments.create(payload, idempotency_key)
```

## Redis

**What it is:** An in-memory store used as pub/sub event bus, circuit
breaker state, Gateway rate-limit counters, and Fraud Agent velocity
windows (see [ADR-0002](decisions/ADR-0002-redis-for-event-bus-and-state.md)).

**Key usage pattern:** A shared `RedisClient` wrapper in `shared/events/`
exposes `publish`/`subscribe` for events and `get_state`/`set_state` (TTL)
for breaker state; the Gateway uses a separate `RateLimiter` helper over
the same connection pool.

```python
await redis_client.publish("payment.created", event.model_dump_json())
```

## PostgreSQL / asyncpg

**What it is:** The relational datastore, one logical schema per service
(see [ADR-0006](decisions/ADR-0006-database-per-service.md)), accessed via
`asyncpg` for non-blocking queries — typically through SQLAlchemy's async
engine.

**Key usage pattern:** Each service owns a connection pool created at
startup; queries go through a `db/` repository layer, never inline in
route handlers.

```python
async with db_pool.acquire() as conn:
    row = await conn.fetchrow(
        "UPDATE users SET balance = balance - $1 WHERE id = $2 RETURNING balance",
        amount, user_id,
    )
```

## OpenTelemetry

**What it is:** Vendor-neutral distributed tracing, used across every
service and agent (see [ADR-0004](decisions/ADR-0004-opentelemetry-for-observability.md)).

**Key usage pattern:** `shared/telemetry/` initializes a tracer at
startup, auto-instruments FastAPI and httpx, and every service propagates
`trace_id` through the Gateway-issued request header and into Redis event
payloads so a trace survives the async boundary.

```python
with tracer.start_as_current_span("fraud.risk_check") as span:
    span.set_attribute("transaction.amount", amount)
    result = await fraud_client.score(payload)
```

## anthropic SDK

**What it is:** The official Python SDK for Claude, used inside the
Healer Agent (diagnosis) and Fraud Agent (risk narrative) — never inside
a service on the synchronous request path.

**Key usage pattern:** Structured output only — the response is parsed
into a Pydantic schema and validated against an action allowlist before
anything executes (see [ADR-0005](decisions/ADR-0005-llm-actions-via-allowlisted-executor.md)).

```python
response = await client.messages.create(
    model="claude-sonnet-5",
    max_tokens=512,
    system=HEALER_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": incident_context}],
)
decision = HealerDecision.model_validate_json(response.content[0].text)
```

## httpx

**What it is:** An async-capable HTTP client, used for every inter-service
call: Gateway→services, Payment→Fraud/User/Provider, Healer→Ops Controller,
Fraud Agent→User Service.

**Key usage pattern:** Every outbound call goes through the shared circuit
breaker wrapper with an explicit timeout — never a bare `httpx.get`/`post`.

```python
async with breaker.guard("payment:provider"):
    resp = await http_client.post(f"{PROVIDER_URL}/capture", json=payload, timeout=3.0)
```

## pydantic-settings

**What it is:** Typed configuration loaded from environment variables and
`.env` files — used identically by every service, every agent, and the
Ops Controller.

```python
class Settings(BaseSettings):
    postgres_url: str
    redis_url: str
    model_config = SettingsConfigDict(env_file=".env")
```

## structlog

**What it is:** Structured (JSON) logging, correlated with the active
`trace_id` at request/event start — the business-event-detail counterpart
to OpenTelemetry's timing/structure data.

```python
log.info("payment.completed", transaction_id=tx.id, amount=tx.amount, provider=tx.provider)
```

## ruff

**What it is:** A fast Python linter (paired with `black` for formatting).
One `ruff.toml` at the repo root applies to every service and agent.

## pytest

**What it is:** The test framework, with `pytest-asyncio` in `asyncio_mode
= "auto"` (root `pyproject.toml`) so async tests need no
`@pytest.mark.asyncio`. Real structure as of Phase 11, three tiers with
three different infra requirements — see
[ADR-0018](decisions/ADR-0018-chaos-testing-measures-real-mttr.md):
`tests/unit/` (no infra, the default for bare `pytest`), `tests/
integration/` and `tests/chaos/` (both need `docker compose up`, run as
their own explicit invocation, skip cleanly if it isn't). One exception
to "everything lives under the root `tests/`": `services/payment/tests/`
is its own pytest run (`cd services/payment && python -m pytest`) — every
service's client code lives under the same `app` package name, so
Payment's PayPal-client tests can't share a session with the root one.

```python
async def test_high_velocity_freezes_once_and_publishes(env):
    a = make_agent(env, velocity_count=10)
    txn = event()
    await a._handle(txn)

    assert len(a.freeze_client.calls) == 1 and a.freeze_client.calls[0] == txn.user_id
    assert env.db.rows[0]["executed"] is True and env.db.rows[0]["decision"]["outcome"] == "frozen"
```
(from `tests/unit/test_fraud_agent.py` — no marker needed, fakes injected
directly rather than pytest fixtures, matching this codebase's style)
