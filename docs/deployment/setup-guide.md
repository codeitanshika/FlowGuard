# FlowGuard — Local Setup Guide

Supersedes the old `SETUP_GUIDE.md` (now in
[../archive/SETUP_GUIDE.md](../archive/SETUP_GUIDE.md)), moved here since
"how to bring the system up" is a deployment concern — local Docker
Compose today, cloud in Phase 13. This guide will be updated as each
phase adds real infrastructure; right now (post-Phase 0) it describes the
target end state so Phase 1–3 have something concrete to build toward.

## Prerequisites

- Docker and Docker Compose (v2+)
- Python 3.12+ (for running scripts/tests outside containers)
- `uv` or `pip` for local dependency management
- An account with the chosen payment provider sandbox (provider selected
  in Phase 10 — PayPal, Razorpay, or Stripe; kept behind an abstraction so
  this section will be filled in with concrete steps once that decision is
  made)
- An Anthropic API key (for the Healer and Fraud agents)

## Environment Variables Explained

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Used by the Healer and Fraud agents for diagnosis/narrative reasoning |
| `POSTGRES_URL` | Base connection string; each service connects using its own least-privilege credential into its own schema (see [../architecture/05-database-schema.md](../architecture/05-database-schema.md)) |
| `REDIS_URL` | Event bus, circuit breaker state, rate limits, velocity windows |
| `JWT_SECRET` | Gateway-only — signs/verifies issued tokens (Phase 2) |
| `<PROVIDER>_CLIENT_ID` / `<PROVIDER>_CLIENT_SECRET` | Sandbox credentials for whichever provider is integrated in Phase 10 |
| `OPS_CONTROLLER_TOKEN` | The one internal token accepted by the Ops Controller, scoped to the Healer Agent (see [ADR-0005](../decisions/ADR-0005-llm-actions-via-allowlisted-executor.md)) |

Never commit `.env` — it's covered by `.gitignore`.

## Docker Compose Startup (target, Phase 3)

```bash
docker compose up --build
```

Startup order by dependency:

1. `postgres`, `redis` (infrastructure)
2. `otel-collector`, `jaeger`
3. `user`, `fraud`, `payment`, `notification` (business services)
4. `gateway` (routes to the above)
5. `ops-controller` (internal-only, no external port)
6. `monitor`, `healer`, `fraud-agent` (control plane)

## Verifying All Services Healthy

Once Phase 1/3 land, every service will answer:

```bash
curl http://localhost:8000/health   # gateway
curl http://localhost:8001/health   # payment
curl http://localhost:8002/health   # fraud
curl http://localhost:8003/health   # user
curl http://localhost:8004/health   # notification
```

```bash
docker compose ps   # every service should show "healthy", not just "running"
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

## Viewing Traces in Jaeger (target, Phase 4)

1. Open `http://localhost:16686`.
2. Select a service (e.g. `payment-service`) and click **Find Traces**.
3. Open a trace to see the full span tree: Gateway → Payment → Fraud →
   Notification (and → Provider on the payment leg).
4. During a fault-injection run, filter by `error=true` and cross-reference
   the trace ID with the `agent_decisions` audit table to see which
   Healer decision that trace triggered.
