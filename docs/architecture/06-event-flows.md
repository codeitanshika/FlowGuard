# FlowGuard — Event Flows

All events are published on Redis pub/sub channels. Every event payload
carries `trace_id` so an event can always be correlated back to the trace
that produced it in Jaeger — this is what lets the Healer Agent pull full
request context for an anomaly it didn't directly observe.

## Channel Reference

| Channel | Publisher | Subscriber(s) | Purpose |
|---|---|---|---|
| `payment.created` | Payment Service | Fraud Agent | trigger async velocity/geo analysis after a transaction is persisted |
| `payment.completed` | Payment Service | Notification Service | tell the user their payment succeeded |
| `payment.failed` | Payment Service | Notification Service | tell the user their payment failed |
| `fraud.user_frozen` | Fraud Agent | Notification Service | tell the user their account was frozen |
| `anomaly.detected` | Monitor Agent | Healer Agent | trigger diagnosis of a threshold breach |
| `incident.diagnosing` | Healer Agent | (dashboard, future) | surface in-progress remediation |
| `incident.resolved` | Healer Agent | (dashboard, future) | surface completed remediation + outcome |
| `circuitbreaker.state_changed` | any breaker-owning service | Monitor Agent | feed breaker flapping into anomaly detection |

## Payload Shapes

### `payment.created`
```json
{
  "event": "payment.created",
  "transaction_id": "uuid",
  "user_id": "uuid",
  "amount": 120.00,
  "currency": "USD",
  "trace_id": "hex",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

### `payment.completed` / `payment.failed`
```json
{
  "event": "payment.completed",
  "transaction_id": "uuid",
  "user_id": "uuid",
  "status": "completed",
  "provider_reference": "prov_ref_123",
  "failure_reason": null,
  "trace_id": "hex",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

### `fraud.user_frozen`
Real as of Phase 9. `risk_assessment_id` is currently always `null` — the
Fraud Agent's velocity/geo decision is independent of Fraud Service's
synchronous per-transaction score, so there is no assessment row to link
to yet (a future correlation is possible but not implemented).
```json
{
  "event": "fraud.user_frozen",
  "user_id": "uuid",
  "reason": "10 transactions in the last 180s (threshold 10)",
  "risk_assessment_id": null,
  "trace_id": "hex",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

### `anomaly.detected`
```json
{
  "event": "anomaly.detected",
  "anomaly_id": "uuid",
  "service": "payment",
  "metric": "error_rate",
  "observed_value": 0.42,
  "threshold": 0.10,
  "severity": "critical",
  "trace_id": "hex",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

### `incident.diagnosing`
```json
{
  "event": "incident.diagnosing",
  "incident_id": "uuid",
  "anomaly_id": "uuid",
  "service": "payment",
  "metric": "error_rate",
  "timestamp": "2026-01-01T00:00:05Z"
}
```

### `incident.resolved`
Published when an incident is closed out, for any outcome. `outcome` was
added in Phase 8: `resolved` (verified recovered/contained, or found stale
and needed no action), `escalated` (nothing the Healer may do; needs a
human), or `failed` (action not applied, or not verified in time).
```json
{
  "event": "incident.resolved",
  "incident_id": "uuid",
  "anomaly_id": "uuid",
  "outcome": "resolved",
  "root_cause": "payment failures are caused by its 'provider' dependency",
  "action_taken": "open-circuit:payment:provider (breaker already open); verified: metric back within thresholds",
  "resolved_at": "2026-01-01T00:02:14Z",
  "timestamp": "2026-01-01T00:02:14Z"
}
```

## End-to-End Flow: Normal Payment

```
Client → Gateway → Payment
  Payment → Fraud (sync risk check)        [low risk]
  Payment → User (debit balance)           [ok]
  Payment → Provider (capture)             [ok]
  Payment persists transaction=completed
  Payment publishes payment.completed → Redis
  Payment publishes payment.created  → Redis   (fired at creation, not completion)
Gateway ← Payment ← 200 OK
Client ← Gateway

(async, already in flight before the client even gets a response)
Redis → Notification Service → user notified
Redis → Fraud Agent → velocity check → within normal bounds → no action
```

## End-to-End Flow: Self-Healing on Provider Failure

This is the flow Phase 6/7/8 will demonstrate live:

```
1. Fault injection makes the Payment Provider Sandbox return errors/timeouts.
2. Payment Service's provider-breaker starts recording failures on each attempt.
3. Error rate for Payment Service rises; every attempt still produces an
   OTel span tagged error=true.
4. Monitor Agent's next polling cycle computes error_rate(payment) > threshold.
5. Monitor Agent persists an `anomalies` row and publishes anomaly.detected.
6. Healer Agent receives the event, pulls recent trace context for
   `service=payment`, and sees the failures are concentrated on the
   provider call specifically (not fraud/user calls).
7. Healer Agent calls the LLM with this structured context; the LLM
   returns a structured diagnosis + proposed action
   (e.g. {"action": "open-circuit", "service": "payment", "dependency": "provider"}).
8. Healer Agent validates the response against the HealerDecision schema
   and the Ops Controller's allowlist.
9. Healer Agent calls Ops Controller → Ops Controller calls Payment
   Service's internal breaker endpoint → breaker transitions to OPEN.
10. New payment requests fail fast on the provider call instead of timing
    out, and (per Payment Service's fallback logic) are marked failed
    immediately rather than hanging.
11. Healer Agent waits out the breaker's cooldown, re-checks metrics; once
    the injected fault is removed/expires, a HALF_OPEN probe succeeds and
    the breaker closes.
12. Healer Agent writes the incident as resolved and publishes
    incident.resolved, including root cause and action taken.
```

## End-to-End Flow: Fraud Freeze

Real and live-verified as of Phase 9 (see [ADR-0016](../decisions/ADR-0016-fraud-agent-simulation-and-freeze-cooldown.md)):

```
1. Payment Service persists a transaction and publishes payment.created.
2. Fraud Agent consumes it, adds the transaction to the user's Redis
   velocity window (key velocity:{user_id}), and gets back the count in
   the last window_seconds (default 180s). It also computes a simulated
   geo-anomaly signal, deterministic per transaction id.
3. risk.decide(velocity_count, geo_anomaly) is the one place the level is
   decided — high/borderline/low — and nothing downstream can change it.
4a. level == "high": the Fraud Agent skips the LLM entirely (no call
    needed — the deterministic rule already has a confident answer) and
    calls User Service's internal freeze endpoint synchronously, unless a
    freeze for this user already happened within the cooldown window (in
    which case it's a no-op, logged, so a burst of high-velocity events
    doesn't spam a freeze_events row per event).
4b. level == "borderline" (velocity in the borderline band, or a geo
    anomaly alone): the Fraud Agent calls the LLM for a narrative/second
    opinion (falls back to a rules-based rationale if the LLM is
    unavailable or fails) and writes it to agent_decisions for human
    review. This never calls the freeze endpoint — there is no path from
    a narrative, however confident, back into the freeze decision.
4c. level == "low": nothing is written; this is the common case for
    normal traffic.
5. (high only) User Service updates `users.status = 'frozen'`, writes a
   `freeze_events` row, and returns success.
6. (high only) Fraud Agent publishes fraud.user_frozen.
7. (high only) Notification Service consumes it and notifies the user
   their account was frozen and why.
```
