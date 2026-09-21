# FlowGuard — Failure Scenarios

For each scenario: what breaks, how the system notices, what happens
automatically, and what the fallback is if automation doesn't resolve it.
These scenarios are the source list for Phase 6 (fault injection) and
Phase 11 (chaos testing).

| # | Scenario | Immediate Impact | Detection | Automated Response | Fallback if automation fails |
|---|---|---|---|---|---|
| 1 | Payment provider sandbox down/timing out | Payment requests hang or fail on the provider call | Provider breaker failure count rises; error-rate span tag; Monitor Agent threshold breach | Provider breaker opens (fast-fail), Healer confirms root cause, incident logged | Breaker stays open past cooldown → alert surfaces in dashboard for manual check |
| 2 | Fraud Service unavailable | Payment requests can't get a risk score | Fraud breaker trips after N failures | Payment falls back to a conservative default (treat as elevated risk, per configured policy) rather than blocking all payments outright | If fallback policy itself is misconfigured, Healer's verification step catches the elevated failure rate and escalates |
| 3 | PostgreSQL connection pool exhausted | Requests to the affected service start failing/queueing | Elevated latency + error spans on DB-bound calls | Monitor flags latency anomaly; Healer's diagnosis distinguishes "dependency down" from "pool exhausted" via span attributes and can trigger a pool-reset action (`/internal/recover`) | If reset doesn't help, incident is marked `failed`, not silently retried forever |
| 4 | Redis unavailable (event bus + breaker state down) | Async flows (notifications, fraud analysis) stall; breaker state reads fail | Redis connection errors surface as spans on every service; breaker code fails closed (see below) | Services treat "can't read breaker state" as **closed by default with a short local in-memory fallback**, never as an excuse to skip the check | This is the one dependency the Ops Controller cannot heal by restarting a *business* service — Redis itself is restarted via the same allowlisted mechanism if it's a container crash, not a config issue |
| 5 | Notification Service backlog/down | Users stop receiving notifications | Consumer lag / Notification Service health check failing | Events remain in the channel history / are safely dropped per at-least-once semantics; payments are entirely unaffected (no synchronous edge exists) | Manual reprocessing from `provider_events`/`transactions` history if needed — out of scope for automated recovery |
| 6 | Gateway overloaded / traffic spike | Rising latency at the edge, possible request drops | Gateway request-rate + latency metrics | Rate limiting already caps per-client load; Healer can request traffic shedding via Ops Controller for a specific noisy client/service | If shedding doesn't bring latency down, it's a capacity problem, not a bug — flagged for manual scaling decision |
| 7 | Cascading retry storm (a naive client retries aggressively into a struggling service) | Load on an already-degraded service increases, delaying recovery | Request volume anomaly correlated with an open breaker | Breaker being OPEN already fails fast, which caps the damage; Gateway rate limiting caps it further per client | If a single client is the cause, Ops Controller's traffic-shed action targets that client specifically |
| 8 | LLM API (Anthropic) unavailable or slow | Healer/Fraud Agent can't get a diagnosis or narrative | LLM call times out per configured budget | Both agents fall back to deterministic-only behavior: Healer uses a rule-based default action for well-known anomaly signatures; Fraud Agent uses the velocity score alone without narrative | If the deterministic fallback has no confident answer, the incident is logged as `diagnosing` and surfaced for manual review rather than guessed at |
| 9 | Circuit breaker flapping (rapid open/close) | Inconsistent behavior, hard to reason about | `circuitbreaker.state_changed` events firing repeatedly in a short window | Monitor Agent treats rapid state changes as its own anomaly signal; Healer can force-hold a breaker open longer than the default cooldown | Manual intervention if flapping persists past a configured number of cycles |
| 10 | Malformed/poison event on a Redis channel | A consumer could crash or infinite-loop retrying a bad message | Consumer error spans + repeated processing-failure logs for the same event ID | Consumers validate every event against its pydantic schema on receipt; invalid events are logged and dropped (not retried), never allowed to crash the consumer loop | Dropped/invalid events are visible in structured logs for manual inspection — no silent data loss beyond that one event |

Scenarios 1 and 2 are implemented and verified as of Phase 5 (circuit
breakers on Payment's three outbound dependencies, real fast-fail and
graceful-degradation behavior) — see
[ADR-0012](../decisions/ADR-0012-timeout-budgets-shrink-toward-the-leaves.md)
for a real bug found testing scenario 2 specifically: Fraud Service's
retry-with-backoff sequence could take longer than the Gateway's own
timeout waiting for Payment's response, so the Gateway would occasionally
give up on a request that was about to succeed via graceful degradation
on its own. Fixed by tightening the retry budget and widening the
Gateway's timeout — the general lesson (a caller's timeout needs
headroom over a callee's *retry* duration, not just its single-attempt
timeout) applies to every row in this table with a retry loop behind it,
not just this one.

## Design Principle Behind This Table

Every row follows the same shape: **detect via telemetry, contain via a
breaker/limit that already exists independent of the AI layer, then let the
agent layer decide whether a smarter action is worth taking.** The system
must survive every one of these scenarios in a *degraded* state even if the
Healer Agent, the LLM, or the Ops Controller itself is unavailable — the
AI layer is an enhancement to resilience, not a dependency of it.
