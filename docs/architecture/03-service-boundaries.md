# FlowGuard — Service Boundaries

Each service owns a single business capability and its own data. No service
reaches into another service's database. Cross-service reads happen over
HTTP; cross-service side effects happen either via a direct internal call
(when an immediate result/ack is needed) or via a Redis event (when the
caller doesn't need to wait).

## API Gateway

**Owns:** authentication (JWT issuance/verification), authorization,
rate limiting, request/trace-ID injection, routing to internal services.
**Owns no business data.**
**Calls:** every internal service, over the internal Docker network only.
**Called by:** the external client — the only component reachable from
outside the network.

## User Service

**Owns:** user identity, account balance, account status (`active` /
`frozen` / `closed`), freeze audit trail.
**Exposes internally:** a freeze/unfreeze endpoint that only the Fraud
Agent and Ops Controller are permitted to call — Fraud Service (the
synchronous scorer) never mutates user state directly, it only returns a
score.
**Calls:** nothing external — User Service is a leaf.
**Called by:** Payment Service (balance check/debit), Fraud Agent
(freeze/unfreeze).

## Payment Service

**Owns:** the transaction state machine, idempotency keys, provider
references, transaction history.
**Calls:** Fraud Service (sync risk check), User Service (balance
check/debit), Payment Provider Sandbox (create/capture) — every one of
these calls is wrapped in its own circuit breaker.
**Publishes:** `payment.created`, `payment.completed`, `payment.failed`.
**Called by:** API Gateway, Ops Controller (internal recovery/breaker
endpoints only).

Payment Service is the orchestrator for the payment business process. It
does not contain fraud logic or user-balance logic — it calls the services
that own those.

## Fraud Service

**Owns:** the synchronous, deterministic risk-scoring rules and the
durable `risk_assessments` record of every score it produces.
**Does not own:** the decision to freeze a user — that authority belongs
to the Fraud Agent (see below), which has behavioral/velocity context
Fraud Service doesn't have.
**Calls:** nothing external in the synchronous path (rules are
self-contained/deterministic to stay fast).
**Called by:** Payment Service (sync, in the request path).

## Notification Service

**Owns:** the outbound notification log (what was sent, to whom, via
which channel).
**Calls:** nothing in the request path — it is purely reactive.
**Called by:** nobody synchronously. It only consumes Redis events
(`payment.completed`, `payment.failed`, `fraud.user_frozen`).

A slow or down Notification Service can never fail or delay a payment,
by construction — there is no synchronous edge into it.

## Monitor Agent

**Owns:** the anomaly-detection logic and the durable `anomalies` record.
**Reads:** OpenTelemetry metrics/traces (via the OTel Collector / a
metrics query interface).
**Publishes:** `anomaly.detected`.
**Has no write access to any business service.** Detection and action are
deliberately separated — Monitor can never itself change system state.

## Healer Agent

**Owns:** the diagnosis workflow and the durable `incidents` /
`agent_decisions` records.
**Reads:** the anomaly event, trace context, current circuit-breaker
state from the reporting service.
**Calls:** the LLM (Anthropic API) for diagnosis, then the **Ops
Controller only** — never a business service directly, never a shell,
never Docker.
**Publishes:** `incident.diagnosing`, `incident.resolved`.

## Fraud Agent

**Owns:** velocity/geo-anomaly analysis (Redis sliding windows) and the
freeze decision workflow.
**Calls:** the LLM for risk narrative/reasoning on ambiguous cases, then
**User Service's internal freeze endpoint** directly (it needs a
definitive ack that the freeze took effect).
**Publishes:** `fraud.user_frozen` (for Notification Service to react to).

The freeze *decision* is agent logic; the freeze *mutation* is owned and
enforced by User Service. The Fraud Agent cannot write to the users table
itself — it can only call User Service's endpoint, which validates the
request the same way it would validate any other caller.

## Ops Controller (Action Executor)

**Owns:** a fixed, named allowlist of operations and the only credential
in the system permitted to run `docker compose restart <service>` or
flip a circuit breaker on another service's behalf.
**Calls:** the target service's internal endpoint, or the container
runtime, for exactly the operation named — nothing else. See
[ADR-0005](../decisions/ADR-0005-llm-actions-via-allowlisted-executor.md)
for why this exists as its own component instead of folding the
capability into the Healer Agent.
**Called by:** Healer Agent only.

## Payment Provider Sandbox

**External.** Owns nothing in FlowGuard. Accessed only through Payment
Service's provider abstraction (see Phase 10), so the concrete provider
(Stripe/Razorpay/PayPal) is swappable without touching Payment Service's
core orchestration logic.

## Synchronous vs. Asynchronous Call Summary

| Caller | Callee | Style | Why |
|---|---|---|---|
| Gateway | any service | sync HTTP | client is waiting |
| Payment | Fraud (risk check) | sync HTTP, circuit-broken | must block a bad transaction before it completes |
| Payment | User (balance) | sync HTTP, circuit-broken | must know the debit succeeded before confirming payment |
| Payment | Provider | sync HTTP, circuit-broken | must know capture succeeded/failed before responding |
| Payment | Redis (`payment.*`) | async publish | downstream consumers don't block the response |
| Fraud Agent | User (freeze) | sync HTTP | needs a definitive ack that the freeze applied |
| Healer Agent | Ops Controller | sync HTTP | needs to verify the action was accepted before checking recovery |
| Monitor Agent | Redis (`anomaly.detected`) | async publish | detection shouldn't block on whether anyone is listening |
