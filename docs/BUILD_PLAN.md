# FlowGuard Build Plan

This is the module-by-module build order for FlowGuard. Each module is meant
to be completed, committed, and demoed independently before moving to the
next — the system should run (in a degraded but real way) after every module.

## Module Overview

| # | Module | Layer | Depends on |
|---|---|---|---|
| 1 | Repo scaffold & shared config | Infra | — |
| 2 | User service | Service | 1 |
| 3 | Payment service (core, no provider) | Service | 2 |
| 4 | PayPal/Razorpay provider integration | Service | 3 |
| 5 | Fraud service | Service | 2 |
| 6 | Notification service | Service | 1 |
| 7 | Redis pub/sub event bus | Infra | 3, 5, 6 |
| 8 | OpenTelemetry instrumentation | Infra | 2–6 |
| 9 | Circuit breaker layer | Resilience | 4, 8 |
| 10 | Supervisor + Healer agents | Agent | 8, 9 |
| 11 | Fraud Analyst + Router agents | Agent | 5, 8, 9 |
| 12 | Dashboard | UI | 8, 10, 11 |

## Module 1 — Repo Scaffold & Shared Config

**What you learn:** Monorepo layout for multi-service Python projects,
`pydantic-settings` for typed env config, shared package conventions.

**What you build:** `shared/` package with config loader, base logging setup,
`.env.example`, Docker Compose skeleton with Redis + PostgreSQL.

**Commit message:** `chore: scaffold repo, shared config, and base compose file`

**Done criteria:** `docker compose up` starts Redis and PostgreSQL; a shared
`Settings` object loads env vars correctly in a throwaway script.

## Module 2 — User Service

**What you learn:** FastAPI project structure, `asyncpg`, basic CRUD, Docker
service definitions.

**What you build:** `services/user` with account creation, balance lookup,
and balance update endpoints backed by PostgreSQL.

**Commit message:** `feat(user): add user service with account and balance endpoints`

**Done criteria:** Can create a user and read/update their balance via HTTP;
service has its own Dockerfile and passes a health check.

## Module 3 — Payment Service (Core, No Provider)

**What you learn:** Service-to-service HTTP calls with `httpx`, transaction
state machines, idempotency keys.

**What you build:** `services/payment` transaction endpoint that validates
input, calls the user service, and records a transaction row — no external
provider call yet.

**Commit message:** `feat(payment): add core transaction flow with user service integration`

**Done criteria:** A transaction request updates user balances and persists
a transaction record with correct status transitions.

## Module 4 — PayPal/Razorpay Provider Integration

**What you learn:** Sandbox payment APIs, provider abstraction interfaces,
secrets handling via env vars.

**What you build:** A `Provider` interface with PayPal and Razorpay
implementations, wired into the payment service.

**Commit message:** `feat(payment): integrate PayPal and Razorpay sandbox providers`

**Done criteria:** A test transaction completes end-to-end against sandbox
credentials for both providers.

## Module 5 — Fraud Service

**What you learn:** Rule-based + heuristic scoring, synchronous service
dependencies, request-time risk checks.

**What you build:** `services/fraud` with a `/score` endpoint the payment
service calls before completing a transaction.

**Commit message:** `feat(fraud): add fraud scoring service and payment integration`

**Done criteria:** High-risk transactions are blocked before hitting the
provider; low-risk transactions pass through unaffected.

## Module 6 — Notification Service

**What you learn:** Fire-and-forget async workflows, decoupling side effects
from the critical path.

**What you build:** `services/notification` with a handler for transaction
outcomes (initially triggered by direct call, before the event bus exists).

**Commit message:** `feat(notification): add notification service for transaction outcomes`

**Done criteria:** A completed or failed transaction produces a visible
notification (log line or webhook call) within the same request cycle.

## Module 7 — Redis Pub/Sub Event Bus

**What you learn:** Event-driven decoupling, pub/sub channel design,
at-least-once delivery tradeoffs.

**What you build:** A shared publisher/subscriber helper in `shared/`;
payment publishes `transaction.completed`/`.failed`; notification subscribes
instead of being called directly.

**Commit message:** `feat(shared): add Redis pub/sub event bus and decouple notification service`

**Done criteria:** Stopping the notification service doesn't block payment
transactions; restarting it resumes processing new events.

## Module 8 — OpenTelemetry Instrumentation

**What you learn:** Distributed tracing, context propagation across HTTP and
pub/sub boundaries, OTel Collector + Jaeger setup.

**What you build:** OTel SDK wired into every service, a collector service
in Docker Compose, and Jaeger for trace visualization.

**Commit message:** `feat(observability): instrument all services with OpenTelemetry and add Jaeger`

**Done criteria:** A single transaction produces one connected trace in
Jaeger spanning payment → fraud → user → notification.

## Module 9 — Circuit Breaker Layer

**What you learn:** Failure-threshold state machines, fast-fail patterns,
fallback routing.

**What you build:** A reusable circuit breaker in `shared/` wrapping provider
calls in the payment service, with open/half-open/closed states surfaced as
metrics.

**Commit message:** `feat(shared): add circuit breaker for provider calls`

**Done criteria:** Forcing repeated provider failures trips the breaker and
subsequent calls fail fast without hitting the provider.

## Module 10 — Supervisor + Healer Agents

**What you learn:** Building a telemetry-consuming agent loop, using the
`anthropic` SDK for anomaly judgment, safe action execution.

**What you build:** `agents/supervisor` (health monitoring, restart
decisions) and `agents/healer` (breaker reset/trip logic).

**Commit message:** `feat(agents): add supervisor and healer agents`

**Done criteria:** Killing a service container triggers a detected outage
and an automated restart within the agent's polling interval.

## Module 11 — Fraud Analyst + Router Agents

**What you learn:** Multi-agent coordination via shared state (Redis),
provider health comparison, adaptive routing.

**What you build:** `agents/fraud_analyst` (threshold tuning on anomaly
patterns) and `agents/router` (shifts traffic between PayPal/Razorpay based
on live health).

**Commit message:** `feat(agents): add fraud analyst and router agents`

**Done criteria:** Degrading one provider's sandbox responses causes the
router to shift new transactions to the other provider automatically.

## Module 12 — Dashboard

**What you learn:** Building an operator UI over live telemetry and agent
decisions, polling/streaming patterns.

**What you build:** `dashboard/` showing service health, circuit breaker
states, recent agent actions, and a fault-injection control panel.

**Commit message:** `feat(dashboard): add operator dashboard for health, breakers, and agent activity`

**Done criteria:** Dashboard reflects a live fault injection (service
down → breaker trip → agent action → recovery) without a page reload.
