# FlowGuard Library Docs

Quick reference for every core dependency: what it is, why FlowGuard uses
it, and the pattern we follow when using it in this project.

## FastAPI

**What it is:** An async Python web framework built on Starlette and
pydantic, used to build all four services' HTTP APIs.

**Why we use it:** Native async support (required for our non-blocking
service-to-service calls), automatic request/response validation via
pydantic, and built-in OpenAPI docs for every service.

**Key usage pattern:** Each service defines its routes in `app/api/`, with
handlers that depend on injected clients (DB pool, Redis client, HTTP
client) via FastAPI's `Depends()`. Handlers stay thin — they call into
`app/services/` for actual logic.

```python
@router.post("/transactions", response_model=TransactionResponse)
async def create_transaction(
    payload: TransactionRequest,
    payments: PaymentService = Depends(get_payment_service),
):
    return await payments.create(payload)
```

## Redis

**What it is:** An in-memory data store used here as a pub/sub message bus
and as shared key/value state for circuit breakers and agent coordination.

**Why we use it:** One lightweight dependency covers our event bus, breaker
state, and agent scratch state, with far less operational overhead than a
dedicated message broker (see `ARCHITECTURE.md` for the Redis-vs-Kafka
decision).

**Key usage pattern:** A shared `RedisClient` wrapper in `shared/` exposes
`publish(channel, event)` / `subscribe(channel)` for events, and
`get_state`/`set_state` (with TTL) for breaker and agent state.

```python
await redis_client.publish("transaction.completed", event.model_dump_json())
```

## PostgreSQL / asyncpg

**What it is:** Our relational datastore (PostgreSQL), accessed via the
`asyncpg` driver for non-blocking queries.

**Why we use it:** Payments and user balances need transactional integrity
(ACID guarantees) that a document or key/value store can't provide; asyncpg
gives us fast native async access without an ORM's overhead.

**Key usage pattern:** Each service owns a connection pool created at
startup and closed at shutdown; queries go through a thin `db/` repository
layer, never inline in route handlers.

```python
async with db_pool.acquire() as conn:
    row = await conn.fetchrow(
        "UPDATE users SET balance = balance - $1 WHERE id = $2 RETURNING balance",
        amount, user_id,
    )
```

## OpenTelemetry

**What it is:** A vendor-neutral observability framework providing
distributed tracing, metrics, and context propagation.

**Why we use it:** It lets every service and agent emit correlated traces
that flow through HTTP calls and Redis events alike, giving the agent layer
a single structured signal to reason over (see `ARCHITECTURE.md` for the
OTel-vs-custom-logging decision).

**Key usage pattern:** Each service initializes a tracer at startup via the
shared `shared/telemetry.py` helper, auto-instruments FastAPI and httpx, and
manually wraps agent decision points in spans.

```python
with tracer.start_as_current_span("fraud.score_transaction") as span:
    span.set_attribute("transaction.amount", amount)
    result = await fraud_client.score(payload)
```

## anthropic SDK

**What it is:** The official Python SDK for calling Claude models, used
inside the agent layer for judgment calls that go beyond fixed thresholds.

**Why we use it:** Agents need to reason over ambiguous signals (is this
error spike a real outage or noise?) — an LLM call gives us that judgment,
while the surrounding agent code keeps the LLM's output constrained to a
structured, validated decision.

**Key usage pattern:** Agents call the Messages API with a tight system
prompt and a pydantic schema for the expected decision, then validate the
parsed response before any action executes.

```python
response = await client.messages.create(
    model="claude-sonnet-5",
    max_tokens=512,
    system=SUPERVISOR_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": telemetry_summary}],
)
decision = SupervisorDecision.model_validate_json(response.content[0].text)
```

## httpx

**What it is:** An async-capable HTTP client for Python.

**Why we use it:** All inter-service calls and provider API calls are async;
`httpx` is the natural counterpart to FastAPI's async stack and supports
connection pooling and timeouts out of the box.

**Key usage pattern:** Every outbound call goes through the shared circuit
breaker wrapper, with an explicit timeout — never a bare `httpx.get`/`post`.

```python
async with breaker.guard("fraud-service"):
    resp = await http_client.post(f"{FRAUD_URL}/score", json=payload, timeout=2.0)
```

## pydantic-settings

**What it is:** A pydantic extension for loading typed configuration from
environment variables and `.env` files.

**Why we use it:** Every service and agent needs typed, validated config
(DB URLs, Redis URLs, API keys, thresholds) at startup — failing fast on a
missing or malformed env var beats discovering it mid-request.

**Key usage pattern:** Each service defines a `Settings(BaseSettings)` class
in `app/core/config.py`, instantiated once and injected via `Depends()`.

```python
class Settings(BaseSettings):
    postgres_url: str
    redis_url: str
    model_config = SettingsConfigDict(env_file=".env")
```

## structlog

**What it is:** A structured logging library for Python that outputs
key-value/JSON log events instead of free-text lines.

**Why we use it:** Structured logs are queryable and correlate cleanly with
OTel trace IDs, which matters when the agent layer or a human operator needs
to reconstruct what happened around an incident.

**Key usage pattern:** A shared logger config binds `trace_id` and `service`
context at request start; every log call passes structured fields, not an
f-string.

```python
log.info("transaction.completed", transaction_id=tx.id, amount=tx.amount, provider=tx.provider)
```

## ruff

**What it is:** A fast Python linter and formatter (replaces flake8, isort,
and black in one tool).

**Why we use it:** Single-tool, single-config linting/formatting keeps every
service consistent without juggling multiple tools or configs; it's fast
enough to run on every commit.

**Key usage pattern:** One `ruff.toml` at the repo root applies to all
services and agents; `ruff check .` and `ruff format .` run in CI and
pre-commit.

## pytest

**What it is:** The standard Python testing framework, used with
`pytest-asyncio` for our async test suites.

**Why we use it:** It's the de facto standard for Python testing, with rich
fixture support that suits our per-service `unit/` and `integration/` test
split.

**Key usage pattern:** Integration tests spin up a test DB/Redis via
fixtures (or `docker compose -f docker-compose.test.yml`); unit tests mock
out I/O boundaries entirely.

```python
@pytest.mark.asyncio
async def test_create_transaction_debits_balance(payment_service, user_factory):
    user = await user_factory(balance=100)
    result = await payment_service.create(TransactionRequest(user_id=user.id, amount=25))
    assert result.status == "completed"
```
