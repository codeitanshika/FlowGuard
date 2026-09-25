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
| [0009](ADR-0009-fixed-window-rate-limiting.md) | Fixed-window rate limiting over sliding-window/token-bucket |
| [0010](ADR-0010-static-client-credentials.md) | API client credentials are static config, not a database |
| [0011](ADR-0011-otlp-direct-to-jaeger.md) | Services export OTLP directly to Jaeger, no separate Collector |
| [0012](ADR-0012-timeout-budgets-shrink-toward-the-leaves.md) | Timeout budgets must shrink toward the leaves of a call chain |
| [0013](ADR-0013-bounded-fault-injection.md) | Fault injection is bounded in duration, intensity, and reach by construction |
| [0014](ADR-0014-monitor-derives-metrics-from-jaeger-traces.md) | The Monitor Agent derives metrics from Jaeger traces, with deterministic thresholds |
| [0015](ADR-0015-healer-proposes-guards-dispose.md) | The Healer proposes, independent guards dispose (layered guards, rule fallback, staleness check) |
| [0016](ADR-0016-fraud-agent-simulation-and-freeze-cooldown.md) | Fraud Agent: simulated geo signal, borderline never freezes, freeze cooldown |
