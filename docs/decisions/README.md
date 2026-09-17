# Architectural Decision Records

Each ADR captures a trade-off: the context that forced a choice, the
decision, and what we're deliberately giving up by making it. Numbered in
the order they were made; a superseded ADR is marked, never deleted.

| ADR | Title |
|---|---|
| [0001](ADR-0001-microservices-over-monolith.md) | Microservices over a modular monolith |
| [0002](ADR-0002-redis-for-event-bus-and-state.md) | Redis for event bus, breaker state, and rate limiting instead of Kafka |
| [0003](ADR-0003-circuit-breaker-over-unbounded-retry.md) | Circuit breaker as the primary resilience mechanism, not unbounded retry |
| [0004](ADR-0004-opentelemetry-for-observability.md) | OpenTelemetry instead of custom logging/APM |
| [0005](ADR-0005-llm-actions-via-allowlisted-executor.md) | LLM decisions execute only through an allowlisted Ops Controller |
| [0006](ADR-0006-database-per-service.md) | Database-per-service (logical) even on one PostgreSQL instance |
| [0007](ADR-0007-deterministic-first-fraud-with-llm-narrative.md) | Deterministic-first fraud scoring; LLM supplies narrative, not the decision |
| [0008](ADR-0008-gateway-centralized-authn-authz.md) | Centralize authn/authz at the API Gateway |
