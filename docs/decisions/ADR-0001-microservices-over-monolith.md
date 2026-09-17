# ADR-0001: Microservices over a Modular Monolith

## Status
Accepted

## Context
FlowGuard could be built as a single well-modularized FastAPI application
with internal module boundaries (payment/, fraud/, user/, notification/)
instead of separate deployable services. A monolith would be faster to
build, easier to debug locally, and would avoid network calls entirely for
what are largely sequential operations in the happy path.

However, the explicit goal of this project is to demonstrate distributed
systems competence: service-to-service failure isolation, independent
circuit breakers per dependency, per-service scaling, and an agent layer
that reasons about *which service* is unhealthy — none of which are
meaningfully demonstrable if everything runs in one process and one
failure domain.

## Decision
Build FlowGuard as independently deployable microservices
(`gateway`, `user`, `payment`, `fraud`, `notification`), each with its own
process, own database schema, and own container.

## Consequences
- **Accepted cost:** real network calls, serialization overhead, and
  distributed-failure modes that a monolith wouldn't have — a dependency
  timing out is now a real scenario to handle, not a hypothetical.
  Local development requires Docker Compose rather than a single `uvicorn`
  process.
- **Benefit:** failure isolation, circuit breakers, and the self-healing
  agent layer all become meaningful and testable, which is the actual
  point of the project.
- **Mitigation:** the whole stack still starts with one command
  (`docker compose up`, Phase 3), so the monolith's "easy to run locally"
  advantage isn't fully lost.

## Alternatives Considered
- **Modular monolith:** rejected — would undercut the distributed-systems
  and resilience-engineering goals that are central to this project.
- **Full event-sourced architecture:** rejected as unnecessary complexity
  for this scope; synchronous calls are used where an immediate result is
  needed (see [03-service-boundaries.md](../architecture/03-service-boundaries.md)),
  events only where decoupling is the actual goal.
