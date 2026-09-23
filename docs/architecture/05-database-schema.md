# FlowGuard — Database Schemas

Database-per-service: each service (and the control-plane subsystem) owns
its own schema. Locally these may live in one PostgreSQL instance under
separate schemas/databases for convenience; no service is permitted to
query another's tables directly — cross-service data needs go through an
API call. See [ADR-0006](../decisions/ADR-0006-database-per-service.md).

All primary keys are UUIDs (avoid leaking sequential IDs externally, and
UUIDs generated client-side make idempotent retries easier to reason
about). All timestamps are `TIMESTAMPTZ`.

## User Service — `flowguard_user`

```sql
CREATE TYPE user_status AS ENUM ('active', 'frozen', 'closed');

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    full_name     TEXT NOT NULL,
    status        user_status NOT NULL DEFAULT 'active',
    balance       NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency      CHAR(3) NOT NULL DEFAULT 'USD',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE freeze_events (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id),
    action                TEXT NOT NULL CHECK (action IN ('freeze', 'unfreeze')),
    reason                TEXT NOT NULL,
    source                TEXT NOT NULL CHECK (source IN ('fraud_agent', 'manual')),
    risk_assessment_id    UUID,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Payment Service — `flowguard_payment`

```sql
CREATE TYPE transaction_status AS ENUM (
    'pending', 'risk_check', 'provider_pending',
    'completed', 'failed', 'cancelled'
);

CREATE TABLE transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    amount              NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    currency            CHAR(3) NOT NULL,
    status              transaction_status NOT NULL DEFAULT 'pending',
    idempotency_key     TEXT NOT NULL,
    provider            TEXT NOT NULL,
    provider_reference  TEXT,
    failure_reason      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key)
);

CREATE TABLE idempotency_keys (
    key                 TEXT PRIMARY KEY,
    transaction_id      UUID NOT NULL REFERENCES transactions(id),
    request_hash        TEXT NOT NULL,
    response_snapshot   JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE provider_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id   UUID NOT NULL REFERENCES transactions(id),
    event_type       TEXT NOT NULL,
    payload          JSONB NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Fraud Service — `flowguard_fraud`

```sql
CREATE TYPE risk_level AS ENUM ('low', 'medium', 'high');

CREATE TABLE risk_assessments (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id    UUID NOT NULL,
    user_id           UUID NOT NULL,
    risk_score        NUMERIC(5,2) NOT NULL,
    risk_level        risk_level NOT NULL,
    rationale         TEXT NOT NULL,
    rule_version      TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Velocity/geo sliding-window counters used by the Fraud Agent are **not**
stored here — they live in Redis as short-TTL sorted sets/counters, since
they're high-write and only need recent history, not permanent storage.
`risk_assessments` is the durable record of each *computed decision*, which
is what audit and evaluation (Phase 15) actually need.

## Notification Service — `flowguard_notification`

```sql
CREATE TYPE notification_channel AS ENUM ('email', 'webhook', 'log');
CREATE TYPE notification_status AS ENUM ('sent', 'failed');

CREATE TABLE notifications (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL,
    transaction_id    UUID,
    channel           notification_channel NOT NULL,
    template          TEXT NOT NULL,
    status            notification_status NOT NULL,
    payload           JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Control Plane (Agents) — `flowguard_control`

Owned collectively by the agent subsystem (Monitor, Healer, Fraud Agent) —
not by any single business service, since anomalies/incidents/decisions are
about the system as a whole.

```sql
CREATE TYPE anomaly_severity AS ENUM ('warning', 'critical');

CREATE TABLE anomalies (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service          TEXT NOT NULL,
    metric           TEXT NOT NULL,             -- 'error_rate' | 'p95_latency' | 'throughput'
    observed_value   NUMERIC NOT NULL,
    threshold        NUMERIC NOT NULL,
    severity         anomaly_severity NOT NULL,
    trace_id         TEXT,
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE incident_status AS ENUM ('diagnosing', 'remediating', 'resolved', 'failed');

CREATE TABLE incidents (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    anomaly_id     UUID NOT NULL REFERENCES anomalies(id),
    status         incident_status NOT NULL DEFAULT 'diagnosing',
    root_cause     TEXT,
    action_taken   TEXT,
    opened_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);

CREATE TABLE agent_decisions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id         UUID REFERENCES incidents(id),
    agent               TEXT NOT NULL,          -- 'monitor' | 'healer' | 'fraud_agent'
    input_summary       JSONB NOT NULL,
    llm_model           TEXT,
    llm_prompt_version  TEXT,
    llm_latency_ms       INTEGER,
    llm_tokens_in        INTEGER,
    llm_tokens_out        INTEGER,
    decision                JSONB NOT NULL,
    validated                 BOOLEAN NOT NULL,
    executed                     BOOLEAN NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`agent_decisions` is deliberately generic enough to log a Healer decision
*or* a Fraud Agent decision — this is the table Phase 14/15 read from for
LLM cost/latency tracking and evaluation accuracy metrics, so it's modeled
now rather than bolted on later. As built, the Healer is its first writer
(`agent` = `healer`, `llm_*` null when the deterministic rules decided);
the Monitor writes only `anomalies`, since its decisions involve no LLM and
the anomaly row is already its full audit record
([ADR-0014](../decisions/ADR-0014-monitor-derives-metrics-from-jaeger-traces.md)).

Added in Phase 8 (not in the original Phase 0 design) — the Ops Controller's
audit log, the record ADR-0005 requires of every proposed action:

```sql
CREATE TABLE ops_actions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action        TEXT NOT NULL,
    params        JSONB NOT NULL,            -- what was actually received (size-bounded)
    caller        TEXT NOT NULL,             -- 'healer' | 'unauthenticated'
    status        TEXT NOT NULL,             -- received | rejected | executed | failed
    detail        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);
```

A row is committed as `received` before authentication is acted on, so
every call to an action endpoint leaves a trace, including rejected ones.
All four control-plane tables are defined once in `shared/control_plane` and
created under a Postgres advisory lock (Monitor, Healer and Ops Controller
start together).

## Ephemeral State (Redis, not Postgres)

| Key pattern | Purpose | TTL |
|---|---|---|
| `breaker:{service}:{dependency}` | circuit breaker state (`closed`/`open`/`half_open`) + failure count | none (explicit transitions) |
| `ratelimit:{client_id}:{window}` | Gateway rate-limit counters | window length |
| `velocity:{user_id}` | Fraud Agent sliding-window transaction timestamps | rolling, e.g. 1h |
| `idempotency:lock:{key}` | short-lived lock to prevent concurrent duplicate processing of the same idempotency key | seconds |
| `fault:{service}:{component}` | injected fault config (ADR-0013) | the fault's `duration_seconds` |
| `monitor:alert:{service}:{metric}` | Monitor alert-dedupe cooldown (ADR-0014) | `MONITOR_ALERT_COOLDOWN_SECONDS` (120) |
| `ops:cooldown:{action}:{service}:{dependency}` | Ops Controller per-target action cooldown | `OPS_ACTION_COOLDOWN_SECONDS` (60) |

These are intentionally excluded from PostgreSQL: they're either
high-write/low-value-per-entry (rate limits, velocity) or must be read with
sub-millisecond latency on the request path (breaker state).
