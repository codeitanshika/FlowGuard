# ADR-0009: Fixed-Window Rate Limiting

## Status
Accepted

## Context
The Gateway needs to cap request volume per client (and, separately, per
IP on the login endpoint, to bound credential-guessing attempts). There
are several standard algorithms — fixed window, sliding window/log, token
bucket, leaky bucket — with real tradeoffs between precision, memory, and
implementation complexity.

## Decision
Use a fixed-window counter in Redis: `INCR` a key namespaced by
`{identity}:{window_bucket}`, `EXPIRE` it on first hit, compare against
the configured limit. Two Redis commands per request on the common path.

## Consequences
- **Accepted cost:** fixed windows allow a burst at the boundary — a
  client could send its full limit right before a window rolls over and
  its full limit again right after, briefly exceeding the intended
  average rate. A sliding-window or token-bucket algorithm doesn't have
  this edge case.
- **Benefit:** trivial to implement correctly, trivial to reason about,
  and cheap — one key per identity per window, auto-expiring, no sorted
  sets or background sweeping. For this project's actual traffic shape
  (bursty-but-bounded test/demo load, not a latency-sensitive SLA), the
  boundary-burst imprecision doesn't matter in practice.

## Alternatives Considered
- **Sliding-window log (Redis sorted set of timestamps):** more precise,
  no boundary burst — rejected for now as more Redis operations and more
  memory per identity than this project's scale justifies.
- **Token bucket:** the standard answer for smoothing bursty traffic
  precisely — rejected for the same reason: real added complexity
  (tracking token count and refill time per identity) for a precision
  gain this project doesn't currently need.

## Revisit Trigger
If burst-at-the-boundary behavior ever actually causes a problem (e.g.
once the Healer Agent's traffic-shedding action needs finer-grained
control than "roughly N per minute"), move to a token bucket then —
not preemptively.
