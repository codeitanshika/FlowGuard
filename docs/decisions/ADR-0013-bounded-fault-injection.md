# ADR-0013: Fault Injection Is Bounded in Duration, Intensity, and Reach by Construction

## Status
Accepted

## Context
Phase 6 needs a way to deliberately break things — inject HTTP 500s,
artificial latency, and simulated timeouts into any service — so Phase 5's
resilience mechanisms (circuit breakers, retries, graceful degradation)
and the later Monitor/Healer agents (Phases 7-8) have something real to
detect and react to. The spec is explicit that this must never become an
uncontrolled destructive capability: a fault injection mechanism is, by
definition, a sanctioned way to make a production-shaped system fail, so
its safety has to come from what the mechanism *can't* do, not from
operators remembering to be careful.

Three distinct ways this could go wrong if left unbounded:
1. A fault configured and then forgotten runs forever.
2. A fault's severity (error rate, latency) is set high enough to be
   indistinguishable from actually taking the service down.
3. An operator locks themselves out of the one thing that could undo a
   fault they just caused — by injecting into the very endpoint used to
   clear faults, or by never designing that endpoint's own reachability
   in the first place.

## Decision
Every one of these is enforced structurally, not by convention:

- **Bounded duration.** `FaultInjector.enable()` writes to Redis with
  `ex=duration_seconds`, clamped to `MAX_DURATION_SECONDS` (5 minutes).
  Expiry is Redis deleting the key itself, not application code
  remembering to check a timestamp and clean up — the one failure mode
  this needs to survive is exactly a struggling, fault-injected service
  not running its own cleanup code reliably.
- **Bounded intensity.** `error_rate` is clamped to `[0.0, 1.0]` and
  `latency_ms` to `[0, MAX_LATENCY_MS]` (60 seconds) at the Pydantic
  model layer (`FaultInjectionRequest`) and again defensively inside
  `enable()` itself. A configured fault can be as disruptive as "always
  fail" but never open-ended in how long a single request hangs.
- **Bounded reach.** `FaultInjectionMiddleware` exempts health/readiness
  paths and every fault-injection control endpoint
  (`_EXEMPT_PATHS`, `_EXEMPT_PREFIXES` in `shared/fault_injection.py`)
  from the faults it applies. This is what stops scenario 3 above: a
  service's own liveness signal and its own "turn this off" endpoint are
  structurally unfaultable, regardless of what's configured for
  everything else. The Gateway's `/api/v1/debug/fault-inject` — the one
  fault-injection control surface meant to be reachable from outside the
  cluster at all — is listed here for the same reason: a 100% error-rate
  fault targeted at the Gateway itself must never be able to prevent
  clearing that exact fault.

## Consequences
- **Accepted cost:** a fault can't be configured to run indefinitely or
  as a true "service is completely gone for good" simulation — the
  ceiling on both duration and latency means every fault is a controlled
  experiment, not an open-ended outage. That's the point, not a
  limitation to route around later.
- **Benefit:** the safety property is enforced at the one place all
  fault injection funnels through (`FaultInjector.enable` and
  `FaultInjectionMiddleware.dispatch`), so no new fault mode or new
  service wiring can accidentally reintroduce an unbounded fault —
  there's no per-call-site opt-out.
- Phases 7-8 (Monitor/Healer agents) can drive fault injection
  programmatically without adding their own safety checks on top: the
  bounds already hold regardless of who or what is calling
  `/internal/fault-injection` or `/api/v1/debug/fault-inject`.

## Alternatives Considered
- **Trust callers to pass reasonable values and document the
  expectation:** rejected — this is exactly the kind of guarantee that
  must not depend on every future caller (including an LLM-driven agent
  in Phase 7+) remembering a convention.
- **A separate "kill switch" endpoint instead of exempting the control
  endpoint from its own middleware:** considered, but it just relocates
  the same problem — the kill switch itself would need the same
  exemption, and now there are two endpoints to keep exempt instead of
  one.
- **Time-limit only, no intensity cap:** rejected — a 5-minute fault at
  100% error rate and 60s latency is already indistinguishable from an
  outage for that duration; a duration cap alone doesn't address
  scenario 2.
