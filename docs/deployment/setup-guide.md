# FlowGuard — Local Setup Guide

Supersedes the old `SETUP_GUIDE.md` (now in
[../archive/SETUP_GUIDE.md](../archive/SETUP_GUIDE.md)). As of Phase 3,
the two workflows below are both real and tested — pick whichever suits
what you're doing.

## Prerequisites

- Docker and Docker Compose (v2+)
- Python 3.12+ and a virtualenv tool, only needed for Workflow B (running
  services directly on the host)
- An account with the chosen payment provider sandbox (provider selected
  in Phase 10 — PayPal, Razorpay, or Stripe; kept behind an abstraction so
  this section will be filled in with concrete steps once that decision is
  made)
- An Anthropic API key (for the Healer and Fraud agents, from Phase 8/9)

## Environment Variables Explained

Copy `.env.example` to `.env` and fill in what's needed for the phases
you're running. Two things worth knowing before you do:

- Each service has its **own** database URL (`USER_DATABASE_URL`,
  `PAYMENT_DATABASE_URL`, ...) and, where relevant, its own downstream
  service URLs (`PAYMENT_FRAUD_SERVICE_URL`, `GATEWAY_PAYMENT_SERVICE_URL`,
  ...) — see [../architecture/05-database-schema.md](../architecture/05-database-schema.md)
  for why each service owns its own database. The values in
  `.env.example` point at `localhost:<port>`, correct for Workflow B; the
  Docker workflow (A) overrides these to container DNS names — see below.
- `GATEWAY_CLIENTS` holds bcrypt-hashed test credentials as a JSON
  string. If you regenerate it, read the comment directly above that
  line in `.env.example` first — Docker Compose interpolates `$` in
  values it reads, including from `.env`, which mangles a bcrypt hash's
  `$2b$12$...` format unless escaped. This is documented in
  `docker-compose.yml` too, at the `GATEWAY_CLIENTS` override.

Never commit `.env` — it's covered by `.gitignore`.

## Workflow A — Full Docker Compose (the whole system, containerized)

```bash
docker compose up --build
```

Brings up, in dependency order (via `depends_on` +
`condition: service_healthy`): `postgres`, `redis`, `jaeger` → `user`,
`fraud`, `notification` → `payment` → `gateway`. Each service's own
`Dockerfile` builds a non-root, multi-stage image; `docker-compose.yml`
overrides database/service URLs to container DNS names (`postgres`,
`redis`, `fraud`, `user`, ...) so the same `.env` secrets work whether
you're running this or Workflow B.

Not yet in `docker-compose.yml` (later phases): `monitor`, `healer`,
`fraud-agent` (Phase 7–9), an `ops-controller` service (Phase 8).

## Workflow B — Services on the Host, Infra in Docker

Faster iteration (no image rebuild per code change) at the cost of only
Postgres/Redis being containerized:

```bash
docker compose -f infra/docker/docker-compose.dev.yml up -d
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # .venv\Scripts\pip on Windows
python -m uvicorn app.main:app --app-dir services/user --port 8003
python -m uvicorn app.main:app --app-dir services/fraud --port 8002
python -m uvicorn app.main:app --app-dir services/notification --port 8004
python -m uvicorn app.main:app --app-dir services/payment --port 8001
python -m uvicorn app.main:app --app-dir services/gateway --port 8000
```

**Don't run A and B at the same time** — both publish the same host
ports (8000–8004, and A's Postgres/Redis would collide with B's if both
happened to publish 5432/6379).

## Verifying All Services Healthy

Same for either workflow:

```bash
curl http://localhost:8000/health   # gateway
curl http://localhost:8001/health   # payment
curl http://localhost:8002/health   # fraud
curl http://localhost:8003/health   # user
curl http://localhost:8004/health   # notification
```

Workflow A additionally shows container-level health:

```bash
docker compose ps   # every service should show "healthy", not just "running"
```

## A Full Request, End to End

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"client_id":"test-merchant","client_secret":"test-secret-123"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['data']['access_token'])")

USER_ID=$(curl -s -X POST http://localhost:8000/api/v1/users \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"email":"you@example.com","full_name":"You","currency":"USD"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['data']['id'])")

# No public deposit endpoint exists yet (see PROJECT_OVERVIEW.md) — seed
# a balance directly via User Service's internal endpoint for testing:
curl -s -X POST "http://localhost:8003/internal/users/$USER_ID/credit" \
  -H "Content-Type: application/json" \
  -d '{"amount":"100.00","currency":"USD","transaction_id":"00000000-0000-0000-0000-000000000001"}'

curl -s -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: my-first-payment" \
  -d "{\"user_id\":\"$USER_ID\",\"amount\":\"10.00\",\"currency\":\"USD\"}"
```

## Running Fault Injection (target, Phase 6)

```bash
curl -X POST http://localhost:8000/api/v1/debug/fault-inject \
  -H "Content-Type: application/json" \
  -d '{"target": "payment-provider", "mode": "timeout", "duration_seconds": 60}'
```

Expected chain: Payment's provider breaker trips → Monitor Agent detects
the error-rate anomaly → Healer Agent diagnoses and opens the breaker
(if not already open) → new requests fail fast instead of hanging → once
the fault expires, the breaker's half-open probe succeeds and it closes →
`incident.resolved` is published. See
[../architecture/06-event-flows.md](../architecture/06-event-flows.md)
for the full sequence.

## Viewing Traces in Jaeger

Every service exports spans to Jaeger as of Phase 4. Make the payment
from "A Full Request, End to End" above (or any request through the
Gateway), then:

1. Open `http://localhost:16686`.
2. Select a service (e.g. `payment`) and click **Find Traces** — or
   search directly by the trace ID from the payment response's
   `X-Trace-Id` header.
3. Open the trace: you'll see one connected span tree covering
   `gateway` → `payment` → `fraud` and `user` (synchronous HTTP calls,
   propagated automatically), plus SQL and Redis spans nested under
   each service, plus — the interesting one — `notification`'s
   `notification.consume.payment.completed` span, linked into the same
   trace even though it ran later, in a different process, kicked off
   by a Redis message rather than a request. See
   [../UNDERSTANDING.md](../UNDERSTANDING.md)'s OpenTelemetry section for
   how that hop specifically gets propagated (it's the one HTTP-based
   auto-instrumentation can't do for free).
4. During a fault-injection run (Phase 6), filter by `error=true` and
   cross-reference the trace ID with the `agent_decisions` audit table
   to see which Healer decision that trace triggered.
