# ADR-0002: Redis for Event Bus, Breaker State, and Rate Limiting instead of Kafka

## Status
Accepted

## Context
FlowGuard needs: (1) a way for Payment Service to notify Notification
Service and the Fraud Agent without a synchronous call, (2) a shared,
fast-read store for circuit breaker state that every request path checks,
and (3) rate-limit counters at the Gateway. Kafka is the conventional
"production" answer for event-driven systems and would look impressive on
a resume line — but it needs to actually fit the problem, not just be
popular.

## Decision
Use Redis for all three needs: pub/sub for events, key/value with TTL for
breaker state and rate limits, sorted sets for the Fraud Agent's velocity
windows.

## Consequences
- **Accepted cost:** Redis pub/sub is fire-and-forget — a subscriber that's
  down when an event fires misses it (no replay, no durable log). This is
  acceptable here because nothing on the *payment* critical path depends on
  event delivery (see [03-service-boundaries.md](../architecture/03-service-boundaries.md)):
  Notification Service missing an event means a delayed/missed
  notification, not a lost transaction (the transaction itself is
  durably recorded in PostgreSQL regardless).
- **Benefit:** one dependency instead of two (Kafka + a cache/state store),
  sub-millisecond reads for breaker-state checks that sit on the request
  path, and near-zero operational overhead for a project run in Docker
  Compose and a small cloud instance.
- **Explicit non-goal:** Redis is not used as a system of record. Anything
  that must survive a restart or be queried historically (transactions,
  risk assessments, incidents, agent decisions) lives in PostgreSQL, never
  only in Redis.

## Alternatives Considered
- **Kafka:** rejected for this scale — durable replay and partition-level
  ordering guarantees aren't needed when nothing consumes events for
  correctness, only for decoupling; the operational cost (broker
  management, consumer group tuning) isn't justified.
- **RabbitMQ:** closer fit than Kafka (proper message broker semantics),
  but still a second infrastructure dependency for guarantees this project
  doesn't need over Redis pub/sub given the "not a system of record" rule.

## Revisit Trigger
If FlowGuard ever needed exactly-once delivery or event replay for audit
reconstruction beyond what the PostgreSQL `agent_decisions`/`incidents`
tables already provide, that would be the point to reconsider Kafka —
documented here so it's a deliberate future decision, not a default.
