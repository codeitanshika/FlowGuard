# ADR-0014: The Monitor Agent Derives Metrics from Jaeger Traces, with Deterministic Thresholds

## Status
Accepted

## Context
The Monitor Agent must turn telemetry into "is this normal?" (error rate,
p95/p99 latency, throughput per service). The Phase 0 LLD assumed it would
query an OTel Collector / metrics backend, but
[ADR-0011](ADR-0011-otlp-direct-to-jaeger.md) deliberately shipped no
Collector and no Prometheus. The only telemetry store that exists is
Jaeger, and its spans already carry duration, status and HTTP status for
every inbound request.

Two more facts shaped this, both found while building it:
- `POST /payments` returns **HTTP 201 with `status=failed`** when a
  dependency is down (a recorded, idempotently replayable outcome). Its
  request span therefore looked healthy, and a span-derived error rate
  would have been blind to a payment-provider outage — the exact scenario
  the self-healing loop exists for.
- A detector that alerts every poll cycle during a sustained outage would
  make the Healer re-remediate one incident indefinitely.

## Decision
1. **Source: Jaeger's query API** (`/api/traces`, the one its own UI
   uses), polled every 15s over a 60s window. Metrics are computed from
   inbound (`span.kind=server`) spans of the monitored service only;
   health probes and control-plane paths (`/health`, `/ready`,
   `/internal/fault-injection`, `/internal/circuit-breakers`,
   `/api/v1/debug`) are excluded so they don't skew rates or throughput.
2. **Deterministic thresholds, no LLM.** Detection must keep working when
   the LLM is unavailable and must be cheap enough to run every 15s
   ([ADR-0007](ADR-0007-deterministic-first-fraud-with-llm-narrative.md)'s
   reasoning). Judgment about *why* belongs to the Healer (Phase 8).
   Thresholds are per-service overridable (`MONITOR_SERVICE_THRESHOLDS`);
   a `min_requests` guard stops tiny samples (1 failure in 2 requests)
   from alerting.
3. **"Error" is defined by what services report.** A span is an error if
   it has `error=true`, ERROR status, or HTTP 5xx. Payment now marks
   dependency-failed payments as ERROR spans (attribute
   `flowguard.failure_kind=dependency`) while still returning 201;
   business rejections (declines, fraud blocks) are deliberately not
   errors. Rule for future services: anything an SRE would call a failure
   must be visible on the span, not only in the response body.
4. **Alert dedupe via a self-expiring Redis key** per (service, metric)
   with a 120s cooldown; a warning that worsens to critical fires once
   more. State is in Redis so it survives Monitor restarts and expires on
   its own ([ADR-0013](ADR-0013-bounded-fault-injection.md)'s reasoning).
5. **Persist, then publish:** the `anomalies` row is written before
   `anomaly.detected` is published so a consumer can always resolve
   `anomaly_id`. If either step fails the dedupe claim is released and
   the next cycle retries.

## Consequences
- **Accepted cost — sampling ceiling:** Jaeger caps traces per query
  (1000 by default here). Above that, counts are undercut; the Monitor
  logs `monitor.telemetry_truncated` and skips the throughput-floor check
  when it happens. Real production volumes need a metrics backend
  (Prometheus/OTel metrics) — a Phase 14 concern, and the detector
  (`detector.py`) is independent of where samples come from.
- **Accepted cost — coupling to an unversioned API:** `/api/traces` is
  Jaeger's UI API, not a stability-guaranteed one. Contained to
  `metrics_client.py`.
- **Accepted cost — latency of detection:** spans are exported in batches
  and evaluated on a 15s cycle, so detection lags an incident by roughly
  5-20s. Acceptable for remediation-grade signals, not for sub-second
  paging.
- **Known gap:** if Jaeger is down the Monitor is blind. It logs
  `monitor.telemetry_unavailable` per service and recovers by itself, but
  raises no anomaly; alerting on the Monitor's own liveness is Phase 14.
- **Deviation from the Phase 0 schema notes:** the Monitor writes only
  `anomalies`, not `agent_decisions`. Its decisions involve no LLM, so the
  anomaly row (observed value, threshold, severity, example trace) is
  the full audit record; `agent_decisions` arrives with the Healer's
  first LLM call.
- **Throughput is computed and logged for every service, but only alerts
  when an operator sets `min_throughput_rps`.** A silence threshold needs
  an expected traffic level, which only the operator knows; a learned
  baseline is deferred rather than guessed at.

## Alternatives Considered
- **Add Prometheus/OTel metrics + Collector now:** the "right" long-term
  answer, rejected for this phase as a scope expansion that
  ADR-0011 explicitly avoided; the traces already contain the signal.
- **Return 5xx for dependency-failed payments so HTTP spans show it:**
  rejected — it breaks the idempotent-replay contract (a stored failed
  outcome is the payment's result, not a transport error).
- **Have services push counters to Redis for the Monitor to read:**
  rejected — a second, parallel telemetry path that could disagree with
  what Jaeger shows humans.
- **LLM-based anomaly detection:** rejected for cost, latency, and
  because the detector must work precisely when the LLM might not.
