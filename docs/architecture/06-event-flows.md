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
```json
{
  "event": "fraud.user_frozen",
  "user_id": "uuid",
  "reason": "velocity anomaly: 14 transactions in 3 minutes",
  "risk_assessment_id": "uuid",
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

### `incident.resolved`
```json
{
  "event": "incident.resolved",
  "incident_id": "uuid",
  "anomaly_id": "uuid",
  "root_cause": "payment-provider sandbox timing out",
  "action_taken": "open-circuit:payment:provider",
  "resolved_at": "2026-01-01T00:02:14Z"
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

```
1. Payment Service persists a transaction and publishes payment.created.
2. Fraud Agent consumes it, adds the transaction to the user's Redis
   velocity window, and recomputes the deterministic velocity score.
3. If the deterministic score alone crosses the high-risk threshold, the
   Fraud Agent proceeds directly to step 5 (no LLM call needed).
4. If the score is in the borderline band, the Fraud Agent calls the LLM
   for a narrative/second opinion — the LLM output only ever supplies the
   *rationale text* and a *confidence signal*, never the freeze/no-freeze
   decision itself (see ADR-0007).
5. If the combined deterministic decision is "high risk," the Fraud Agent
   calls User Service's internal freeze endpoint synchronously.
6. User Service updates `users.status = 'frozen'`, writes a
   `freeze_events` row, and returns success.
7. Fraud Agent publishes fraud.user_frozen.
8. Notification Service consumes it and notifies the user their account
   was frozen and why.
```
