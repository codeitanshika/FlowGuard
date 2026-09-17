# ADR-0003: Circuit Breaker as the Primary Resilience Mechanism, Not Unbounded Retry

## Status
Accepted

## Context
When Payment Service calls Fraud Service, User Service, or the payment
provider, any of those can be slow or down. The naive response — retry
until it works — makes an already-struggling dependency worse: every
failed request retries, multiplying load on the failing target right when
it can least handle it, and delays the caller from ever finding out
something is wrong.

## Decision
Wrap every outbound inter-service and provider call in a circuit breaker
(CLOSED → OPEN → HALF_OPEN → CLOSED), with breaker state stored in Redis
so it's shared across replicas of the same service and readable by the
Monitor/Healer agents. Bounded retry with exponential backoff is still
used, but only *inside* a CLOSED breaker for genuinely transient blips —
it is never the primary defense against a sustained outage.

## Consequences
- **Accepted cost:** requests fail fast (and visibly) once a breaker opens,
  rather than eventually succeeding after a long wait — callers must
  handle a `DEPENDENCY_UNAVAILABLE` response deliberately (e.g. Payment
  Service's documented fallback policy when the Fraud breaker is open,
  see [07-failure-scenarios.md](../architecture/07-failure-scenarios.md)).
- **Benefit:** a failing dependency's blast radius is capped — it can't
  consume unbounded caller resources or retry-storm itself into staying
  down longer. It also gives the Healer Agent a discrete, observable state
  machine to reason about and act on, which unbounded retry doesn't offer.
- **Per-dependency granularity:** Payment Service holds three independent
  breakers (`fraud`, `user`, `provider`) rather than one breaker for "any
  outbound call" — Fraud Service being down must never trip the breaker
  guarding User Service calls.

## Alternatives Considered
- **Retry-only with backoff:** rejected as the primary mechanism — it has
  no notion of "stop trying," so it doesn't actually protect a struggling
  dependency, only slows down how fast it gets hammered.
- **Bulkhead pattern (connection pool isolation) alone:** complementary,
  not a substitute — isolating resource pools limits *this service's*
  exposure but doesn't stop calls from reaching an unhealthy dependency the
  way a breaker's fast-fail does. Considered a possible Phase 5 addition,
  not a replacement for the breaker.
