# FlowGuard Architecture

## Overview

FlowGuard is a multi-agent, self-healing payment processing platform built as a
set of independent microservices coordinated by autonomous agents. The system
accepts payments through multiple providers (PayPal, Razorpay), screens them
for fraud, and uses a supervising agent layer to detect, diagnose, and recover
from failures without human intervention — restarting unhealthy services,
tripping circuit breakers, rerouting traffic, and escalating only when
automated recovery isn't possible.

The core design goal is **resilience through observability**: every service
emits traces, metrics, and structured events; agents consume that telemetry
in real time and act on it.

## System Layers

```
┌─────────────────────────────────────────────┐
│                 Dashboard (UI)               │
├─────────────────────────────────────────────┤
│              Agent Layer (agents/)           │
│  Supervisor · Fraud Analyst · Healer · Router │
├─────────────────────────────────────────────┤
│             Service Layer (services/)         │
│  payment · fraud · user · notification        │
├─────────────────────────────────────────────┤
│              Shared Infrastructure            │
│  Redis (pub/sub + state) · PostgreSQL · OTel  │
└─────────────────────────────────────────────┘
```

- **Dashboard layer** — operator-facing view of service health, agent
  decisions, and live traces.
- **Agent layer** — long-running processes that observe telemetry and act
  (see Agent Design below).
- **Service layer** — stateless FastAPI microservices that do the actual
  payment work.
- **Shared infrastructure** — Redis, PostgreSQL, and the OpenTelemetry
  collector, used by every service and agent.

## Service Map

| Service | Responsibility | Depends on |
|---|---|---|
| `services/payment` | Accepts payment requests, talks to PayPal/Razorpay, orchestrates the transaction lifecycle | fraud, user, Redis, PostgreSQL |
| `services/fraud` | Scores transactions for risk, blocks/flags suspicious activity | user, Redis |
| `services/user` | Owns user/account records and balances | PostgreSQL |
| `services/notification` | Sends transaction outcomes to users (email/webhook/log sink) | Redis (pub/sub) |

## Agent Design

Agents are separate from services — they never sit in the request path of a
payment. Instead they subscribe to telemetry and events, and act through
well-defined control channels (restart, circuit-break, reroute).

| Agent | Watches | Can do |
|---|---|---|
| Supervisor | Health checks, OTel traces, error rates | Restart a service, escalate to human |
| Fraud Analyst | Fraud service decisions, anomaly patterns | Tighten fraud thresholds, quarantine a user |
| Healer | Circuit breaker state, dependency failures | Trip/reset breakers, reroute traffic to fallback provider |
| Router | Provider latency/error rates (PayPal vs Razorpay) | Shift new transactions to the healthier provider |

Agents reason over telemetry using the `anthropic` SDK for judgment calls
(e.g. "is this error spike a blip or a real outage?") and take deterministic
action through a small, auditable action API — the LLM never touches
production data directly, it only emits an action decision that a
non-LLM executor validates and applies.

## Data Flow

1. Client calls `services/payment` with a transaction request.
2. Payment service calls `services/fraud` (sync) for a risk score.
3. If approved, payment service calls the selected provider (PayPal/Razorpay)
   and updates `services/user` balances.
4. Payment service publishes a `transaction.completed` (or `.failed`) event
   to Redis pub/sub.
5. `services/notification` consumes the event and notifies the user.
6. Every hop emits OTel spans to the collector; agents tail this stream
   continuously, independent of the transaction path.
7. On anomaly, the relevant agent publishes a control event (e.g.
   `circuit.trip:paypal`) that services subscribe to and enforce locally.

## Tech Decisions

### Why Redis over Kafka
FlowGuard's event volume and retention needs are modest — this is a
demonstration-scale payment platform, not a high-throughput ledger. Redis
gives us pub/sub for events, a fast key/value store for circuit-breaker and
agent state, and simple TTL-based caching, all from one dependency with near
-zero operational overhead. Kafka's durability and replay guarantees aren't
needed here, and its operational cost (ZooKeeper/KRaft, partitioning,
consumer group tuning) isn't justified for this project's scope.

### Why circuit breaker over retry
Blind retries against a degraded payment provider make outages worse — they
amplify load on an already-struggling dependency and delay detection. A
circuit breaker gives the system a fast-fail state: once failures cross a
threshold, calls stop immediately, the Router agent can shift traffic to a
healthy provider, and the breaker probes for recovery on its own schedule.
Retries are still used *inside* a closed breaker for transient blips, but the
breaker is the primary resilience mechanism.

### Why OTel over custom logging
OpenTelemetry gives FlowGuard vendor-neutral, correlated traces across every
service and agent decision out of the box (trace/span IDs propagate through
Redis events and HTTP calls). A custom logging solution would require
reinventing context propagation, sampling, and exporter integrations
(Jaeger, Prometheus, etc.) that OTel already provides, and it would be far
harder for the agent layer to reason over unstructured logs than over
structured, queryable spans.

## Detailed Workflow Diagram

```
Client
  │
  ▼
[payment service] ──sync──▶ [fraud service] ──▶ [user service]
  │                                 │
  │ (approved)                      │ (risk score)
  ▼                                 ▼
[PayPal/Razorpay via circuit breaker]
  │
  ▼
publish: transaction.completed / transaction.failed  ──▶ Redis pub/sub
                                                            │
                                                            ▼
                                                  [notification service]

                     (in parallel, continuously)
[all services] ──spans/metrics──▶ [OTel Collector] ──▶ [Jaeger / Prometheus]
                                          │
                                          ▼
                                  [agents/: Supervisor, Healer,
                                   Fraud Analyst, Router]
                                          │
                                          ▼
                             control events (circuit.trip, reroute,
                             restart) ──▶ Redis ──▶ back to services
```
