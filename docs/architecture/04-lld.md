# FlowGuard — Low-Level Design

Internal module breakdown per component. This is what Phase 1+ code must
conform to — folder names here match `services/<name>/app/...` in the
project structure.

## API Gateway (`services/gateway`)

```
app/
├── api/          # route handlers: proxy routes, /auth/login
├── core/          # settings, JWT verification, rate limiter
├── middleware/      # trace-id injection, request logging, error envelope
└── routing/           # per-service route table (path -> internal URL)
```

- **Owns:** JWT issuance/verification, rate-limit counters (Redis).
- **State:** none durable — rate-limit counters are ephemeral in Redis.
- **Breakers:** one per downstream service it proxies to.

## User Service (`services/user`)

```
app/
├── api/          # /users, /users/{id}/balance, /internal/users/{id}/freeze
├── core/          # settings
├── models/         # UserCreate, UserResponse, FreezeRequest (pydantic)
├── db/               # SQLAlchemy models + repository functions
└── services/           # UserService: create, get, debit, freeze, unfreeze
```

- **DB tables owned:** `users`, `freeze_events`.
- **Sync dependencies:** none (leaf service).
- **Breakers owned:** none (nothing to call out to).

## Payment Service (`services/payment`)

```
app/
├── api/          # /payments (POST/GET), /internal/circuit-breakers/*, /internal/recover
├── core/          # settings, idempotency middleware
├── models/         # PaymentRequest, PaymentResponse, TransactionState
├── db/               # transactions, idempotency_keys, provider_events
├── clients/            # FraudClient, UserClient, ProviderClient (each breaker-wrapped)
└── services/               # PaymentOrchestrator: the transaction state machine
```

- **DB tables owned:** `transactions`, `idempotency_keys`, `provider_events`.
- **Sync dependencies:** Fraud Service, User Service, Payment Provider.
- **Breakers owned:** `fraud`, `user`, `provider` (three independent breakers
  — one dependency degrading never trips the others).
- **Publishes:** `payment.created`, `payment.completed`, `payment.failed`.

## Fraud Service (`services/fraud`)

```
app/
├── api/          # /internal/risk-check, /risk-assessments/{transaction_id}
├── core/          # settings, rule thresholds
├── models/         # RiskCheckRequest, RiskAssessment
├── db/               # risk_assessments
└── services/           # RuleEngine: deterministic scoring rules
```

- **DB tables owned:** `risk_assessments`.
- **Sync dependencies:** none — rules are self-contained so this call
  stays fast and can't itself become the thing that trips a breaker
  upstream unnecessarily.

## Notification Service (`services/notification`)

```
app/
├── api/          # /notifications (GET, for audit/debug)
├── core/          # settings
├── models/         # NotificationRecord
├── db/               # notifications
├── consumers/           # Redis subscriber: payment.completed/.failed, fraud.user_frozen
└── services/               # Dispatcher: picks channel (log/email/webhook), sends
```

- **DB tables owned:** `notifications`.
- **Sync dependencies:** none.
- **Consumes:** `payment.completed`, `payment.failed`, `fraud.user_frozen`.

## Monitor Agent (`agents/monitor`)

```
agent.py          # main loop: poll metrics -> evaluate -> persist -> publish
metrics_client.py    # queries Jaeger's trace API (ADR-0014; no Collector, ADR-0011)
thresholds.py           # configurable per-service thresholds
detector.py                # error rate / p95 / p99 / throughput + breach grading
alert_state.py                # Redis cooldown dedupe (suppress repeats, allow escalation)
db.py                            # `anomalies` model + persistence
config.py
main.py                             # FastAPI shell: /health, /ready, runs the loop
```

- **Loop:** poll on a fixed interval (default 15s over a 60s window),
  compute metrics per service, compare to threshold, and on a
  non-suppressed breach persist to `anomalies` then publish
  `anomaly.detected`. The event's `trace_id` is an example trace from the
  window (latest errored request, or slowest request), not the Monitor's
  own — open it in Jaeger to see the failure.
- **DB tables owned:** `anomalies` (in the shared control-plane schema,
  see [05-database-schema.md](05-database-schema.md)).
- **No write access to any business service — detection only.**

## Healer Agent (`agents/healer`)

```
agent.py             # subscribe anomaly.detected; one task per incident; verification; close-out
context.py              # trace evidence (sanitized span facts) + breaker state
diagnosis.py                # LLM call (messages.parse -> HealerDecision) with rule fallback
rules.py                       # deterministic fallback for unambiguous error-rate signatures
schemas.py                        # AnomalyEvent (in), HealerDecision (LLM output, data only)
allowlist.py                         # planner: escalate / no-op / execute (pre-flight, not enforcement)
verifier.py                             # recovery check + pre-action staleness check
ops_client.py                              # the Healer's only route to changing anything
db.py                                          # incidents, agent_decisions
config.py, main.py
```

- **Loop:** subscribe `anomaly.detected` (invalid events logged and
  dropped) → open incident → gather evidence → LLM proposes (rules if it
  cannot) → planner → staleness pre-check → Ops Controller → verify (the
  Monitor's own detector, up to 5 min) → close → publish
  `incident.resolved`. Every incident ends `resolved` or `failed`; see
  [ADR-0015](../decisions/ADR-0015-healer-proposes-guards-dispose.md).
- **DB tables owned:** `incidents`, `agent_decisions` (control-plane
  tables in `shared/control_plane`, shared with the Monitor and Ops
  Controller; created under a Postgres advisory lock).
- **Calls:** Anthropic API (optional), Ops Controller, Jaeger (read-only).
  Nothing else. Reuses `agents/monitor` detection modules and the Ops
  Controller's allowlist definition rather than duplicating them.

## Fraud Agent (`agents/fraud`)

```
agent.py             # main loop: subscribe payment.created -> handle
velocity.py              # Redis sliding-window transaction counting
geo.py                       # simulated geo-anomaly check
risk.py                          # combines deterministic signals into a risk decision
narrative.py                        # LLM call for the human-readable rationale only
schemas.py                             # FraudAgentDecision pydantic model
db.py                                      # persist agent_decisions
config.py
```

- **Loop:** subscribe `payment.created` → update velocity window → compute
  deterministic risk factors → if borderline, ask LLM for a narrative/
  second opinion → if high risk, call User Service's freeze endpoint →
  publish `fraud.user_frozen`.
- **DB tables owned:** shares `agent_decisions`; velocity counters live in
  Redis, not Postgres.
- **The freeze threshold is a deterministic number, not an LLM output**
  — see [ADR-0007](../decisions/ADR-0007-deterministic-first-fraud-with-llm-narrative.md).

## Ops Controller (`agents/ops_controller` — internal service)

```
allowlist.py     # the allowlist (pure code) + parameter validation
api.py              # GET /ops/actions, GET /ops/circuits, one explicit POST route per action
executors.py           # the actual breaker operations; URLs from static settings only
audit.py                  # ops_actions row written BEFORE auth/validation/execution
db.py, config.py, main.py
```

- **The allowlist is code, not configuration the LLM can influence.**
  Implemented: `open-circuit` and `reset-circuit` on Payment's `fraud` /
  `user` / `provider` breakers. `restart-service` and `shed-traffic` are
  not implemented (Docker-socket access; Gateway support) — adding one is
  a reviewed code change.
- **Called by:** Healer Agent only, via a bearer token (`OPS_HEALER_TOKEN`)
  that only the Healer holds. Published on the host's loopback only.
- **Enforcement:** authenticate → audit → validate → per-target cooldown
  (60s) → execute. Rejected and unauthenticated calls are audited too.

## Shared Package (`shared/`)

```
shared/
├── events/      # Redis publish/subscribe helpers, event schemas
├── schemas/       # cross-service pydantic models (e.g. TraceContext)
├── telemetry/        # OTel setup shared by every service/agent
└── config/               # base Settings class, env loading
```
