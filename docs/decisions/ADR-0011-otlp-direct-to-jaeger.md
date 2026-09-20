# ADR-0011: Services Export OTLP Directly to Jaeger, No Separate Collector

## Status
Accepted

## Context
The canonical OpenTelemetry deployment pattern has every service export
spans to an **OTel Collector**, which then forwards them to one or more
backends (Jaeger, a metrics store, a logging pipeline, ...). The
Collector adds real value once you need batching/retry policy in one
place, sampling decisions, fan-out to multiple backends, or protocol
translation. FlowGuard's Phase 0 architecture diagram sketched a
Collector as a distinct box between services and Jaeger, following that
convention.

## Decision
Every service's `OTEL_EXPORTER_OTLP_ENDPOINT` points straight at
Jaeger's own OTLP/HTTP receiver (`:4318`) — Jaeger (since 1.35) accepts
OTLP natively, so it doesn't need anything in front of it to receive
spans from an OTel SDK. No `otel-collector` container exists in
`docker-compose.yml`.

## Consequences
- **Accepted cost:** no central place to apply a sampling policy, batch
  toward multiple backends, or transform spans before they land — every
  service's exporter talks to exactly one destination it's configured
  with directly. Adding a second consumer of this telemetry (a metrics
  pipeline, a different trace backend) means touching every service's
  config, not just the Collector's.
- **Benefit:** one fewer moving part for a single-backend, single-
  environment local/demo setup — nothing to build, containerize, or
  configure beyond Jaeger itself, which was already justified on its own
  merits (see [ADR-0004](ADR-0004-opentelemetry-for-observability.md)).
- **Consistent with this project's general bias**: don't stand up
  infrastructure ahead of a concrete need for it (same reasoning as
  [ADR-0002](ADR-0002-redis-for-event-bus-and-state.md)'s Redis-over-Kafka
  call) — a Collector earns its place once there's an actual second
  consumer of this telemetry or a sampling requirement, not preemptively.

## Alternatives Considered
- **OTel Collector in front of Jaeger, as the Phase 0 diagram sketched:**
  rejected for now — it's the "textbook" shape, but there's no concrete
  requirement yet (single backend, no sampling policy, single
  environment) that it would actually serve. The diagram described an
  eventual/typical shape, not a Phase 4 commitment.

## Revisit Trigger
Add a Collector when any of these become real: a second telemetry
consumer (e.g. a metrics backend feeding Phase 14's dashboards), a need
for head- or tail-based sampling as trace volume grows, or centralized
processing (redaction, enrichment) that shouldn't live in every
service's own config.
