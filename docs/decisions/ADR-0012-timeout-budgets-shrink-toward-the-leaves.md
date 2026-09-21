# ADR-0012: Timeout Budgets Must Shrink Toward the Leaves of a Call Chain

## Status
Accepted

## Context
Found via testing, not inspection, while verifying Phase 5's circuit
breaker: with Fraud Service stopped, Payment's fraud-breaker retry
sequence (2 attempts at the client's 3s timeout, plus backoff delays)
could take up to ~10.2 seconds — longer than the Gateway's own 10-second
proxy timeout to Payment Service. Payment's graceful-degradation logic
(see [ADR-0007](ADR-0007-deterministic-first-fraud-with-llm-narrative.md)-
adjacent reasoning in `payment_orchestrator.py`) was working correctly
every time — the payment always eventually succeeded — but the *Gateway*
would sometimes give up waiting and return 503 before Payment finished
its own internal retry sequence and responded. A caller correctly
tolerating a slow dependency doesn't help if a caller *further up* the
chain times out waiting for the tolerant caller.

## Decision
Every timeout budget in a call chain must leave real headroom over the
worst-case duration of everything it calls, including that callee's own
retries — not just its single-attempt timeout. Concretely:
`CircuitBreakerConfig.max_retries` defaults to 1 (2 attempts), not 2,
and `FraudClient`/`UserClient`'s per-call timeout is 2.0s, not 3.0s —
worst case per breaker-guarded call is now ~4.2s. The Gateway's proxy
timeout is 20s, nearly 5x that — headroom on top of headroom, not just
the minimum needed to squeak by.

## Consequences
- **Accepted cost:** a genuinely stuck (not just slow) Payment Service
  call now takes up to 20s to fail at the Gateway instead of 10s —
  slower to surface a total hang. Acceptable: a total hang is rare and
  the breaker itself fails fast once open; this budget mostly matters
  for the CLOSED-state retry sequence, which is now well inside it.
- **Benefit:** the actual bug class this closes — a well-behaved,
  gracefully-degrading downstream call getting cut off by an upstream
  caller's impatience — is a common, easy-to-miss source of spurious
  5xxs in real systems. It doesn't show up in a single service's own
  tests, only when the full chain is exercised together, which is
  exactly how this one was found.
- **This is a general rule for this codebase, not a one-off tuning**:
  any new inter-service call with its own retry/backoff must be checked
  against every caller's timeout above it in the chain, not just given a
  timeout that looks reasonable in isolation.

## Alternatives Considered
- **Just raise the Gateway's timeout and leave Payment's retry budget
  alone:** rejected as the sole fix — it treats the symptom (this one
  chain happened to be close) rather than the general principle (retry
  budgets compound going down the chain, timeouts must compound going
  up it). Both changes landed together for defense in depth.
- **Remove retries from the fraud-check call entirely, since it degrades
  gracefully anyway:** considered — a single fast failure would sidestep
  this whole class of bug for that one call site. Rejected for now: the
  retry still recovers genuinely transient blips (a dropped packet, not
  a real outage) without ever reaching degraded mode, which is a better
  outcome than degrading unnecessarily. Revisit if Fraud Service's real-
  world failure mode turns out to be mostly-instant or mostly-sustained
  rather than transient.
