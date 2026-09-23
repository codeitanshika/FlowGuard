# ADR-0015: The Healer Proposes, Independent Guards Dispose

## Status
Accepted. Implements [ADR-0005](ADR-0005-llm-actions-via-allowlisted-executor.md).

## Context
Phase 8 lets the system change itself in response to an anomaly. ADR-0005
fixed the principle (an LLM never reaches a shell or an arbitrary
endpoint; it only produces data that a fixed allowlisted executor may
act on). Building it surfaced the decisions below, three of them from
bugs found only by running the whole loop live against injected faults.

## Decision

**1. Layered guards; the LLM is the weakest link by design.**
```
telemetry (untrusted text) -> LLM proposes HealerDecision (data only)
  -> schema validation -> Healer planner (allowlist, confidence, breaker state)
  -> staleness pre-check -> Ops Controller (independent re-validation,
     audit row first, cooldown) -> executor -> verification
```
Each layer assumes the previous one is compromised. The Ops Controller
re-validates everything itself: its allowlist is code
(`open-circuit` / `reset-circuit` on Payment's three breakers), targets
come from static settings so no request can choose a URL, callers are
authenticated by a token only the Healer holds (constant-time compare,
minimum length enforced at startup), and every call — including
unauthenticated, malformed, and cooldown-blocked ones — is written to
`ops_actions` *before* anything else happens. `restart-service` and
`shed-traffic` are deliberately not implemented yet: the first needs
Docker-socket access, the second needs Gateway support that does not
exist; adding either is a reviewed code change, which is the point.

**2. LLM first, deterministic rules as the fallback — never the reverse
dependency.** The LLM (`claude-opus-5`, structured output, 30s timeout)
proposes; any failure — no key, HTTP error, refusal, truncation, invalid
output — falls back to a narrow rule set (failure scenario 8). Rules act
only on unambiguous error-rate evidence and escalate everything else. The
system works with no `ANTHROPIC_API_KEY` at all. Untrusted telemetry is
sanitized and travels only as JSON data in the user message, never in the
system prompt. Server-side refusal `fallbacks` are not used: a refusal is
handled by the deterministic rules, which is a stronger fallback than a
second model.

**3. Low confidence or off-allowlist means escalate, and escalation
executes nothing.** A decision outside the allowlist is recorded
`validated=false`; a low-confidence one `validated=true, executed=false`.
The decision row is written before the Ops call and marked executed only
after it succeeds.

**4. Do not act on a stale anomaly.** *Found by live testing:* after an
outage ended and Payment's own half-open probe closed the breaker, the
Monitor's re-alert (its 2-minute cooldown expired while its 60s window
still held the tail of the outage) reached the Healer, which force-opened
the healthy breaker and caused ~30s of self-inflicted failures. Before
`open-circuit` the Healer now inspects only the most recent requests; if
they no longer fail, or there is no recent traffic to confirm, the
incident closes with no action. A remediation system that can cause the
outage it is fixing needs this guard more than it needs any other.

**5. A cooldown proves the action ran, not that it still holds.** *Found
by live testing:* an Ops 429 was treated as "already applied" while
something had since reset the breaker. On 429 the Healer re-reads breaker
state and proceeds only if it is in the target state.

**6. Silence is not containment.** *Found by live testing:* an incident
resolved as "contained" because a test simply stopped sending traffic.
For a guarded dependency, no traffic counts as containment only while the
breaker is still open or half-open; for a caller like Payment, no traffic
is never recovery.

**7. "Recovered" means "the Monitor would not alert."** Verification
re-runs the Monitor's own detector (`agents/monitor`) over a short window
every 15s for up to 5 minutes, so the two agents cannot disagree about
what healthy means. The Healer image copies only the Monitor's detection
modules and the Ops allowlist definition, not their services.

**8. Every incident reaches a terminal state.** Each incident is its own
task; crashes, Ops errors, timeouts, overload, and Healer restarts (a
startup sweep closes incidents left by a dead process) all end in
`resolved` or `failed`, and an `incident.resolved` event carrying an
`outcome` of `resolved`, `escalated` or `failed` (an addition to the
Phase 0 payload).

## Consequences
- **Known gap — the LLM path has not been exercised against the real
  API** (no credentials were available when this was built). It is
  covered by unit tests that drive the real SDK over a mock HTTP
  transport (request shape, `effort`, JSON schema, refusal, truncation,
  invalid output) and every live test ran the rules path. The first run
  with a key should be watched, and prompt version `healer-v1` is
  recorded on each LLM decision row for later evaluation.
- **Known gap — anomaly delivery is not durable.** `anomaly.detected`
  is Redis pub/sub ([ADR-0002](ADR-0002-redis-for-event-bus-and-state.md)):
  if the Healer is down when it is published, that anomaly is lost (the
  Monitor re-alerts after its cooldown if the problem persists).
- **Known gap — threshold duplication.** The Healer has its own copy of
  the threshold settings and must be kept in sync with the Monitor's.
- **Known gap — unknown routes are not audited** (a request to a route
  that does not exist is a 404 before any handler runs); only calls to
  real action endpoints are.
- **Accepted cost:** an open-circuit is a temporary shield, not a cure.
  While a fault persists the breaker will keep cycling and the Monitor
  will re-alert; each cycle produces a new incident, bounded by the
  Ops cooldown and the staleness check.
- Phase 15 can evaluate the LLM directly from `agent_decisions`
  (input summary, model, prompt version, latency, tokens, decision,
  validated, executed) against ground truth from fault injection.

## Alternatives Considered
- **Let the Healer call Payment's breaker endpoints directly:** rejected;
  it would put a second credential and a second enforcement point in the
  component we trust least (ADR-0005).
- **LLM-only, no rules:** rejected; the AI layer must never be a
  dependency of resilience.
- **Rules-only:** rejected as the end state; rules cannot handle
  ambiguous evidence, which is where the LLM earns its place. They are
  the floor, not the ceiling.
- **Trust the anomaly as received:** rejected after it caused the very
  outage it was meant to fix (decision 4).
