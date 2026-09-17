# ADR-0004: OpenTelemetry Instead of Custom Logging/APM

## Status
Accepted

## Context
The Monitor Agent needs a reliable, structured signal of "how is the
system behaving right now" across five services and three agents, and the
Healer Agent needs to pull the *specific* request context behind a given
anomaly. Free-text logs could technically carry this information, but
turning unstructured log lines into a queryable, cross-service signal an
agent can reason over would mean reinventing context propagation,
correlation, and querying from scratch.

## Decision
Instrument every service and agent with OpenTelemetry: auto-instrumented
FastAPI/httpx spans plus manually created spans around agent decision
points, all exported to an OTel Collector and visualized in Jaeger.
`trace_id` is propagated through HTTP headers *and* through Redis event
payloads, so a trace stays connected even across the async event boundary.

## Consequences
- **Accepted cost:** every service takes on an OTel SDK dependency and
  some instrumentation boilerplate at startup; there's a small amount of
  overhead per request for span creation/export.
- **Benefit:** vendor-neutral, standard trace format; Jaeger gives a human
  a visual trace tree for free; the Monitor Agent gets structured
  metrics (error rate, latency percentiles, throughput) without writing a
  custom aggregation pipeline; the Healer Agent can pull the exact spans
  behind an anomaly by trace ID instead of grepping logs.
- **Structured logging (`structlog`) is complementary, not redundant:**
  logs carry business-event detail ("transaction created", "breaker
  opened") tagged with the active `trace_id`, while OTel carries timing
  and cross-service structure. Neither replaces the other.

## Alternatives Considered
- **Custom logging + manual correlation IDs:** rejected — would require
  building span-like context propagation by hand, with no Jaeger-equivalent
  visualization, for a worse result than adopting an existing standard.
- **A commercial APM (Datadog, New Relic):** rejected for this project —
  introduces a paid external dependency and vendor lock-in for a
  portfolio-scale system where OTel + self-hosted Jaeger fully covers the
  requirement.
