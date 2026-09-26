# FlowGuard — Local Setup Guide

Supersedes the old `SETUP_GUIDE.md` (now in
[../archive/SETUP_GUIDE.md](../archive/SETUP_GUIDE.md)). As of Phase 3,
the two workflows below are both real and tested — pick whichever suits
what you're doing.

## Prerequisites

- Docker and Docker Compose (v2+)
- Python 3.12+ and a virtualenv tool, only needed for Workflow B (running
  services directly on the host)
- Optional: a PayPal Developer sandbox account (Phase 10) — only needed to
  run with `PAYMENT_PROVIDER_BACKEND=paypal`; the default (`mock`) needs
  nothing here
- An Anthropic API key (for the Healer and Fraud agents, from Phase 8/9) —
  also optional, both fall back to deterministic rules without one

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
curl http://localhost:8005/health   # monitor agent
curl http://localhost:8006/health   # ops controller (loopback only)
curl http://localhost:8007/health   # healer agent
curl http://localhost:8008/health   # fraud agent
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

## Running Fault Injection (Phase 6)

Every call needs a bearer token whose client has the `debug:write` scope
(`test-merchant` in the default local dev clients — see `.env.example`;
`readonly-client` deliberately doesn't have it, to exercise a 403).
`target` is one of `gateway`, `payment`, `fraud`, `user`, `notification`,
or `payment-provider` (Payment Service's in-process mock provider — see
[ADR-0013](../decisions/ADR-0013-bounded-fault-injection.md)). `gateway`
is handled in-process by the Gateway itself; every other target is
forwarded to that service's own `/internal/fault-injection`.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"client_id":"test-merchant","client_secret":"test-secret-123"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['data']['access_token'])")

# error_500: every matching request fails with a real-looking 500
curl -X POST http://localhost:8000/api/v1/debug/fault-inject \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"target": "fraud", "mode": "error_500", "error_rate": 1.0, "duration_seconds": 60}'

# latency / timeout: sleeps in place before responding, capped at 60s
curl -X POST http://localhost:8000/api/v1/debug/fault-inject \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"target": "payment-provider", "mode": "timeout", "latency_ms": 30000, "duration_seconds": 60}'

# Clear a fault before it expires on its own
curl -X DELETE "http://localhost:8000/api/v1/debug/fault-inject?target=fraud" \
  -H "Authorization: Bearer $TOKEN"
```

Every fault is bounded — 5 minutes max duration, 60s max injected
latency, auto-cleared by Redis TTL even if never explicitly disabled
(ADR-0013). Health/readiness endpoints and every fault-injection control
endpoint (including this one on the Gateway itself) are permanently
exempt from faults, so a self-inflicted 100% error rate on `gateway`
can never lock you out of clearing it — verified by injecting exactly
that and confirming the DELETE above still goes through.

Full observable chain today: normal request → inject a fault → error
rate/latency rises on that dependency → Payment's circuit breaker (Phase
5) reacts (opens, degrades gracefully, or fails fast depending on which
dependency) → once the fault expires, the breaker's half-open probe
succeeds and it closes. The Monitor/Healer-driven detect-and-remediate
loop described in
[../architecture/06-event-flows.md](../architecture/06-event-flows.md)
is the Phase 7-8 continuation of this same chain and isn't built yet —
today, watch the breaker react via each service's structured logs
(`breaker.open`, `breaker.half_open`, `breaker.closed`).

## Running the Monitor Agent (Phase 7)

The Monitor starts with the rest of the stack (`docker compose up`) and
needs no manual steps, **but** it stores anomalies in a new
`flowguard_control` database that only gets created on a *fresh* Postgres
volume. If you had a stack running before Phase 7, either run
`docker compose down -v` first (wipes local data) or create it once:
`docker compose exec postgres psql -U flowguard -c "CREATE DATABASE flowguard_control;"`.

Every 15s it logs one `monitor.snapshot` line per service (requests, error
rate, p95/p99, throughput over the last 60s):

```bash
docker compose logs -f monitor | grep monitor.snapshot
```

Watch the events it publishes, then break something (needs the `TOKEN`
from the fault-injection section above, and at least 10 requests in the
window — below that the Monitor deliberately stays quiet):

```bash
docker compose exec redis redis-cli subscribe anomaly.detected   # terminal 1

curl -X POST http://localhost:8000/api/v1/debug/fault-inject \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"target":"payment-provider","mode":"error_500","duration_seconds":180}'
# ...send ~15 payments (see "A Full Request, End to End")...
```

Within about 15-30 seconds a critical `payment` `error_rate` anomaly
appears in that terminal and in the database:

```bash
docker compose exec postgres psql -U flowguard -d flowguard_control \
  -c "select service, metric, observed_value, threshold, severity, trace_id from anomalies order by detected_at"
```

Paste the `trace_id` into Jaeger (`http://localhost:16686`) to open an
example failing request. Repeat breaches of the same service+metric are
suppressed for 2 minutes (`MONITOR_ALERT_COOLDOWN_SECONDS`) so a sustained
outage produces one event, not one every 15s. Common gotchas: no anomaly
=> fewer than 10 requests in the window, or the payment returned 201
`failed` before the Payment image was rebuilt with the span-tagging change;
`monitor.telemetry_unavailable` => Jaeger is down (the Monitor recovers on
its own). `latency` faults need `latency_ms` above 1000 to cross the
default p95 warning threshold.

## Running the Healer Agent and Ops Controller (Phase 8)

Both start with `docker compose up` (fresh volume needed for the shared
`flowguard_control` database, as in the Monitor section). With no
`ANTHROPIC_API_KEY` the Healer decides from its deterministic rules and logs
`healer.llm_disabled`; set the key in `.env` to let the LLM propose instead
(any LLM failure falls back to the rules).

**Try the Ops Controller's safety boundary directly** (loopback only; token is
`OPS_HEALER_TOKEN` from `.env`):

```bash
T=local-dev-healer-token-change-me
curl -X POST http://127.0.0.1:8006/ops/actions/open-circuit \
  -H "Content-Type: application/json" -d '{"service":"payment","dependency":"fraud"}'        # 401
curl -X POST http://127.0.0.1:8006/ops/actions/open-circuit -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" -d '{"service":"payment","dependency":"database"}'     # 400 off-allowlist
curl -X POST http://127.0.0.1:8006/ops/actions/restart-service -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" -d '{"service":"payment"}'                             # 404 no such action
curl http://127.0.0.1:8006/ops/circuits -H "Authorization: Bearer $T"                        # breaker states
```

Every call to an action endpoint, including the rejected ones, is in the audit
table:

```bash
docker compose exec postgres psql -U flowguard -d flowguard_control \
  -c "select action, caller, status, detail from ops_actions order by created_at"
```

**Watch the whole self-healing loop.** Steady traffic is essential — the
Monitor needs 10+ requests in its window and the Healer needs traffic to
verify recovery. Log in **once** and reuse the token (logging in per request
trips the Gateway's own 10/min login limit). Then break the Fraud Service
partially (35% errors is enough to alert but not to trip Payment's breaker):

```bash
curl -X POST http://localhost:8000/api/v1/debug/fault-inject -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target":"fraud","mode":"error_500","error_rate":0.35,"duration_seconds":180}'
# ...keep a payment loop running (one payment every ~0.5s)...
docker compose logs -f monitor healer | grep -E "anomaly_detected|healer\."
```

Expected within ~30s: `monitor.anomaly_detected` (fraud, error_rate) →
`healer.incident_opened` → `healer.decision` (source `rules`, plan `execute`) →
`healer.action_executed` → after ~30s `healer.incident_closed` (`resolved`,
"contained: breaker holding and no traffic reaches the failing service").
Inspect the record:

```bash
docker compose exec postgres psql -U flowguard -d flowguard_control \
  -c "select status, root_cause, action_taken from incidents order by opened_at"
docker compose exec postgres psql -U flowguard -d flowguard_control \
  -c "select agent, validated, executed, decision->'plan' plan, input_summary->>'source' source from agent_decisions"
```

Provider outage (`"target":"payment-provider"`, `error_rate` 1.0): Payment's
own breaker usually trips first, so the Healer infers `provider` from the
trace, sees the breaker already open, takes **no** action, and verifies
recovery once the fault expires (a later re-alert is recognised as stale and
also produces no action). Common gotchas: no incident => fewer than 10
requests in the Monitor's window, or the same (service, metric) alerted in
the last 2 min (Monitor cooldown; `redis-cli del monitor:alert:<svc>:<metric>`
to reset while testing); `429` from the Ops Controller => the same action on
the same target ran in the last 60s (`OPS_ACTION_COOLDOWN_SECONDS`); an
incident stuck `remediating` is still verifying (up to 5 min) and is closed
as failed if the Healer restarts.

## Running the Fraud Agent (Phase 9)

Starts with `docker compose up` (same fresh-volume note as the Monitor/Healer
— it writes to the shared `flowguard_control` database). With no
`ANTHROPIC_API_KEY` it uses a rules-based narrative for borderline cases and
logs `fraud_agent.llm_disabled`.

**Trigger a freeze.** Log in once, create a user, fund it, then send 10+
payments quickly (the velocity window is 180s by default, so they need to
land inside it):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"client_id":"test-merchant","client_secret":"test-secret-123"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['data']['access_token'])")
UID=$(curl -s -X POST http://localhost:8000/api/v1/users -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" -d '{"email":"you@example.com","full_name":"You","currency":"USD"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['data']['id'])")
curl -s -X POST "http://localhost:8003/internal/users/$UID/credit" -H "Content-Type: application/json" \
  -d '{"amount":"1000.00","currency":"USD","transaction_id":"00000000-0000-0000-0000-000000000009"}'

for i in $(seq 1 10); do
  curl -s -o /dev/null -X POST http://localhost:8000/api/v1/payments -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: fr-$i" \
    -d "{\"user_id\":\"$UID\",\"amount\":\"1.00\",\"currency\":\"USD\"}"
done

docker compose logs fraud-agent | grep -E "fraud_agent\.(assessed|flagged_for_review|user_frozen)"
```

Expected: `level: borderline` starting around the 6th-7th payment (each one
logged for review, never frozen), then `fraud_agent.user_frozen` at exactly
the 10th. Confirm the user is actually frozen and further payments are
rejected:

```bash
curl -s "http://localhost:8000/api/v1/users/$UID" -H "Authorization: Bearer $TOKEN"   # status: "frozen"
curl -s -X POST http://localhost:8000/api/v1/payments -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: fr-11" \
  -d "{\"user_id\":\"$UID\",\"amount\":\"1.00\",\"currency\":\"USD\"}"   # status: "failed", "not active"
```

Send a few more payments for the same user right away and confirm the
freeze isn't repeated (one row, not several):

```bash
docker compose exec postgres psql -U flowguard -d flowguard_user \
  -c "select action, reason, source, created_at from freeze_events order by created_at"
docker compose exec postgres psql -U flowguard -d flowguard_control \
  -c "select agent, executed, decision->>'level' level, decision->>'outcome' outcome from agent_decisions where agent='fraud_agent' order by created_at"
```

Gotchas: fewer than 6 requests in the 180s window never triggers anything
(by design — `low` writes nothing); a second burst for the *same* user
within `FRAUD_AGENT_FREEZE_COOLDOWN_SECONDS` (default 300s) after a freeze
logs `outcome: skipped_cooldown` instead of freezing again; the geo-anomaly
signal is simulated (no real location data exists in this system — see
ADR-0016) and fires for roughly 1 in 10 transactions independent of
velocity, so an occasional `borderline` on the very first payment for a
user is expected, not a bug.

## Running with the Real PayPal Provider (Phase 10)

By default (`PAYMENT_PROVIDER_BACKEND=mock`, no PayPal account needed) the
stack behaves exactly as in every earlier phase. To exercise the real
integration instead:

1. Create a sandbox app at
   [developer.paypal.com/dashboard/applications/sandbox](https://developer.paypal.com/dashboard/applications/sandbox)
   and copy its Client ID and Secret.
2. In `.env`, set:
   ```
   PAYMENT_PROVIDER_BACKEND=paypal
   PAYPAL_CLIENT_ID=<your sandbox client id>
   PAYPAL_CLIENT_SECRET=<your sandbox secret>
   ```
3. Rebuild just Payment Service: `docker compose up --build -d payment`.
   With `provider_backend=paypal` and no credentials set, the container
   won't start at all (`PAYMENT_PROVIDER_BACKEND=paypal requires
   PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET`) — that's the intended
   fail-fast behavior, not a bug.

Send a normal payment (see "A Full Request, End to End" above) and check
`"provider"` in the response — it now says `"paypal"`. **A real capture
today always declines** with `failure_reason` mentioning `ORDER_NOT_APPROVED`
or similar: this system has no buyer-approval redirect step anywhere, and
PayPal's Orders API genuinely requires one for a wallet-style order with
no payment source — see
[ADR-0017](../decisions/ADR-0017-paypal-as-the-real-provider-backend.md).
This is PayPal correctly declining, not a broken integration: watch
`docker compose logs payment` for the real HTTPS calls to
`api-m.sandbox.paypal.com`, and confirm the transaction is marked
`failed` with a real PayPal error message, not a connection error.

Two supporting endpoints, both 404 unless `paypal` is the active backend:

```bash
# Status lookup (FR13) — needs a real capture id, so this 404s on its own
# fault (RESOURCE_NOT_FOUND) unless you have one from a successful capture.
curl http://localhost:8001/internal/providers/paypal/captures/<capture_id>

# Webhook receiver — additionally needs PAYPAL_WEBHOOK_ID (create a webhook
# for your sandbox app in the same dashboard, subscribed to at least
# PAYMENT.CAPTURE.COMPLETED, and set PAYPAL_WEBHOOK_ID to its id). PayPal
# calls this directly (it's never routed by the Gateway); the request's
# own PAYPAL-* signature headers are what authenticate it, verified via
# PayPal's own /v1/notifications/verify-webhook-signature endpoint.
```

To go back to the mock provider, set `PAYMENT_PROVIDER_BACKEND=mock` (or
remove the line — that's the default) and rebuild Payment Service again.

## Running Integration and Chaos Tests (Phase 11)

Three test tiers, each with a different infra requirement — see
[ADR-0018](../decisions/ADR-0018-chaos-testing-measures-real-mttr.md):

```bash
python -m pytest                    # unit — no infra needed, ~5s (the default)
```

**Integration** (`tests/integration/`) drives the real, running stack over
its public/internal HTTP APIs — no mocks, no direct DB/Redis access:

```bash
docker compose up --build -d
python -m pytest tests/integration -v   # ~1 minute, 17 tests
```

Covers the payment happy path, idempotency replay, provider declines,
Gateway auth/authz, the fault-injection safety property from ADR-0013,
and the Fraud Agent freeze flow — the automated versions of what earlier
phases verified by hand.

**Chaos** (`tests/chaos/`) additionally needs Postgres/Redis reachable
from the host, to read authoritative `anomalies`/`incidents` timestamps
rather than guessing recovery from HTTP behavior — a compose *override*
publishes those ports, applied only for this:

```bash
docker compose -f docker-compose.yml -f infra/docker/docker-compose.test.yml \
  up --build -d
python -m pytest tests/chaos -v         # ~2-3 minutes, 2 tests
```

Each test injects a real fault (failure scenarios #1 and #2), drives real
traffic, and asserts the resulting incident resolves within NFR11's 120s
budget. For an actual number to quote rather than a pass/fail:

```bash
python -m tests.chaos.report --runs 3
```

A real run: both scenarios recovered 100% of the time, mean MTTR
58-60s. Both suites skip cleanly with an actionable message if their
prerequisites aren't met — no need to remember which command needs what.

To go back to normal local dev afterward: `docker compose down` (drop
`-f infra/docker/docker-compose.test.yml` — the override doesn't persist
on its own) then `docker compose up --build -d` as usual.

## CI/CD (Phase 12)

Design and trade-offs: [ADR-0019](../decisions/ADR-0019-cicd-build-once-promote-the-artifact.md).
Three workflows in `.github/workflows/`:

| Workflow | Runs | Does |
|---|---|---|
| `ci.yml` | every PR, every push to `main` | lint + format check, unit tests (Python 3.12 and 3.13, plus Payment's own run), bandit / pip-audit / gitleaks, build + Trivy-scan all nine images, full-stack integration suite. On `main`, once all pass: publish images to GHCR as `sha-<commit>`, then run the integration suite against those published images ("staging") |
| `promote.yml` | pushing a `v*` tag | re-tags that commit's already-built images as the release and `production` — no rebuild; refuses if the commit never passed CI |
| `chaos.yml` | nightly + on demand | the MTTR chaos tests, uploading the report |

**Run the same gates locally** before pushing (tool versions pinned in
`infra/ci/requirements-tools.txt`):

```bash
pip install -r requirements-dev.txt -r infra/ci/requirements-tools.txt
ruff check . && ruff format --check .        # `ruff format .` to fix style
python -m pytest && (cd services/payment && python -m pytest)
bandit -r agents services shared infra -x "*/tests/*" -q
python infra/ci/audit_dependencies.py         # needs network (PyPI advisories)
```

**One-time repository setup** — the workflows can't do these for you:

1. *Settings > Environments*: create `staging` (no rules needed) and
   `production` with **Required reviewers** enabled. That approval is the
   only thing stopping a version tag from promoting unattended.
2. *Settings > Branches*: protect `main` and require the `CI` checks
   (Lint and format, Unit tests, Security scans, the image builds,
   Integration tests) before merging.
3. *Settings > Actions > General*: workflow permissions can stay at the
   default read-only; each job that needs `packages: write` asks for it.

**Cut a release:** merge to `main`, wait for its CI run (including
*Staging verification*) to go green, then
`git tag v1.0.0 && git push origin v1.0.0` and approve the `production`
deployment when prompted. The promoted images are
`ghcr.io/<owner>/flowguard-<service>:production` and `:v1.0.0`. Rolling them
out to a host is Phase 13 — the *Deploy* job only says so.

**Reproduce staging locally** with images you have (or pull from GHCR):

```bash
IMAGE_REGISTRY=ghcr.io/<owner> IMAGE_TAG=sha-<commit> \
docker compose -f docker-compose.yml -f infra/docker/docker-compose.images.yml \
  up -d --no-build --wait
```

**Not yet confirmed on GitHub:** these workflows pass `actionlint` and
GitHub's published schemas and every command in them was run locally, but
they had not run on GitHub's runners when this was written. Watch the first
run in the *Actions* tab; failures there should be about permissions, the
registry or caching rather than about the code.

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
