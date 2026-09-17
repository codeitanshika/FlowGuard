# FlowGuard Architecture Docs

Phase 0 system design. Read in this order:

1. [01-requirements.md](01-requirements.md) — functional and non-functional requirements, explicit non-goals
2. [02-hld.md](02-hld.md) — high-level design, component diagram, why every component exists
3. [03-service-boundaries.md](03-service-boundaries.md) — what each service/agent owns and how they're allowed to call each other
4. [04-lld.md](04-lld.md) — internal module breakdown per service/agent
5. [05-database-schema.md](05-database-schema.md) — schema per service, ephemeral Redis state
6. [06-event-flows.md](06-event-flows.md) — Redis channels, payload shapes, end-to-end flows
7. [07-failure-scenarios.md](07-failure-scenarios.md) — failure table used to drive Phase 6 (fault injection) and Phase 11 (chaos testing)

Related:
- [../api/api-contracts.md](../api/api-contracts.md) — endpoint-level API contracts
- [../decisions/](../decisions/) — architectural decision records (trade-offs)
