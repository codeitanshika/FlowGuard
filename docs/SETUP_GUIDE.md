# FlowGuard Setup Guide

## Prerequisites

- Docker and Docker Compose (v2+)
- Python 3.12+ (for running scripts/tests outside containers)
- `uv` or `pip` for local dependency management
- A PayPal Developer account (sandbox)
- A Razorpay account (test mode)
- An Anthropic API key (for the agent layer)

## Sandbox Account Setup (PayPal + Razorpay)

### PayPal
1. Sign in at the PayPal Developer Dashboard and create a new sandbox app.
2. Copy the app's **Client ID** and **Client Secret**.
3. Under sandbox accounts, ensure a default sandbox **business** account
   (receiver) and **personal** account (payer) exist — create them if not.
4. Set the app's webhook/return URLs to point at your local `payment`
   service once it's running (e.g. `http://localhost:8000/webhooks/paypal`).

### Razorpay
1. Sign in to the Razorpay Dashboard and switch to **Test Mode**.
2. Go to Settings → API Keys and generate a test **Key ID** and **Key
   Secret**.
3. Note the test card/UPI credentials Razorpay provides for simulating
   successful and failed payments.

## Environment Variables Explained

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `PAYPAL_CLIENT_ID` | PayPal sandbox app client ID, used by `services/payment` to authenticate with PayPal's API |
| `PAYPAL_CLIENT_SECRET` | PayPal sandbox app secret — keep out of version control |
| `ANTHROPIC_API_KEY` | Used by every agent in `agents/` to call Claude for anomaly judgment and decision-making |
| `POSTGRES_URL` | Connection string shared by `user` and `payment` services for their PostgreSQL database |
| `REDIS_URL` | Connection string used for the pub/sub event bus and circuit breaker/agent state |

If you add Razorpay credentials, extend `.env.example` with
`RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` following the same pattern.

Never commit `.env` — it's already covered by `.gitignore`.

## Docker Compose Startup

```bash
# from the repo root
docker compose up --build
```

This starts, in order of dependency:

1. `postgres` and `redis` (infrastructure)
2. `otel-collector` and `jaeger`
3. `user`, `fraud`, `payment`, `notification` services
4. `agents` (supervisor, healer, fraud-analyst, router)
5. `dashboard`

To start only infrastructure and iterate on one service locally:

```bash
docker compose up postgres redis otel-collector jaeger
uvicorn app.main:app --reload --app-dir services/payment
```

## Verifying All Services Healthy

Each service exposes health endpoints:

```bash
curl http://localhost:8000/health   # payment
curl http://localhost:8001/health   # fraud
curl http://localhost:8002/health   # user
curl http://localhost:8003/health   # notification
```

All should return `{"status": "ok"}`. The dashboard (`http://localhost:3000`
by default) also shows a consolidated health grid — use it as the quick
at-a-glance check once it's built (Module 12).

You can also check container-level health directly:

```bash
docker compose ps
```

Every service should show `healthy`, not just `running`.

## Running Fault Injection

FlowGuard includes a fault-injection mode for demonstrating self-healing.
Once the fault-injection endpoint/CLI is in place:

```bash
# simulate the payment provider timing out
curl -X POST http://localhost:8000/debug/fault-inject \
  -H "Content-Type: application/json" \
  -d '{"target": "paypal", "mode": "timeout", "duration_seconds": 60}'
```

Watch the dashboard: the circuit breaker for PayPal should trip, the Router
agent should shift new transactions to Razorpay, and the event should appear
in the agent activity log — all without manual intervention.

To simulate a hard service crash instead:

```bash
docker compose stop fraud
```

The Supervisor agent should detect the outage via failed health checks and
attempt a restart.

## Viewing Traces in Jaeger

1. Open the Jaeger UI at `http://localhost:16686`.
2. Select a service (e.g. `payment-service`) from the **Service** dropdown.
3. Click **Find Traces** to see recent transaction traces.
4. Open a trace to see the full span tree — payment → fraud → user →
   notification — including timing for each hop.
5. During a fault-injection run, filter by tag (e.g. `error=true`) to find
   the traces that triggered agent action, and cross-reference the trace ID
   with the agent activity log to see the resulting decision.
