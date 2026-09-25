# ADR-0018: Chaos Tests Measure Real MTTR from Authoritative Timestamps, Not Inferred Behavior

## Status
Accepted

## Context
Phase 11 (FR: "unit, integration, API, failure, agent tests;
MTTR/recovery-rate measurement") needs to prove NFR11 — mean time to
automated recovery for a detected anomaly under 2 minutes in the local/
demo environment — rather than assert it from design alone. Three
questions needed answers before writing a single test.

## Decision

**1. Three test tiers, each with a different infra requirement, kept out
of each other's way.** `tests/unit/` (existing, no infra) stays the
default for bare `pytest`/`python -m pytest` — `testpaths` was narrowed
from `["tests"]` to `["tests/unit"]` specifically so adding the two new
directories couldn't slow down or change that default. `tests/
integration/` needs the stack up (`docker compose up --build -d`) and
drives it purely over its public/internal HTTP APIs — no direct DB/Redis
access, so it needs no infra changes beyond what already exists.
`tests/chaos/` needs the stack up **and** a way to read authoritative
recovery timestamps (see decision 2) — both run as their own explicit
`python -m pytest tests/<dir>` invocation, each skipping cleanly with an
actionable message if its prerequisites aren't met, rather than being
silently included in — or silently failing — a default `pytest` run.

**2. MTTR is measured from `flowguard_control`'s own `anomalies.
detected_at`/`incidents.resolved_at`, not inferred from HTTP behavior.**
An earlier design considered measuring "time until payments succeed
again" purely from the outside — appealing because it needs no DB
access, but wrong for at least the provider-outage scenario: while a
100%-error-rate fault is still injected, payments *can't* succeed again
no matter how well the system has responded; what's actually recoverable
and worth measuring is *whether the system correctly identified and
contained the failure*, which requires reading what the Monitor and
Healer actually decided. This needs Postgres reachable from the host,
which it isn't by default (`docker-compose.yml` deliberately doesn't
publish 5432/6379 — collision risk with a host database). Rather than
changing that default, `infra/docker/docker-compose.test.yml` is a
compose *override* that publishes both ports, applied only for chaos
runs (`docker compose -f docker-compose.yml -f infra/docker/docker-
compose.test.yml up --build -d`). `tests/integration/` never needs it.

**3. Two scenarios, matching the two already marked "implemented and
verified as of Phase 5" in `07-failure-scenarios.md`** — provider outage
and Fraud Service outage — rather than all ten. The table's other
scenarios (Redis down, Gateway overload, LLM unavailable, ...) don't yet
have the same load-bearing automated response to measure; adding chaos
coverage for them is future work tracked by that same table, not
invented here to pad scenario count.

**4. This suite is what found two real bugs, not what confirmed a design
was already correct** — the point of chaos testing, working exactly as
intended:
   - **No reconnect on a dropped Redis connection.** The Healer's, Fraud
     Agent's, and Notification's consumer loops each called
     `bus.subscribe()` once and looped on `get_message()` with no
     reconnect logic. A chaos run that (incidentally, via an unrelated
     `docker compose` operation) restarted Redis mid-session killed all
     three consumer tasks outright — logged loudly where a done-callback
     existed, silently where one didn't (Notification) — while every
     affected process kept reporting itself healthy via `/health` the
     whole time. Fixed with `shared/events/consume_forever()`: the same
     reconnect-with-backoff wrapper for all three.
   - **The Healer's incident verifier could get permanently stuck once a
     breaker self-healed on its own.** A *partial*-failure fault (40%
     error rate, not 100%) meant a half-open probe had a real chance of
     succeeding on its own, closing the breaker independently of anything
     the Healer did. The verifier's containment check required the
     breaker to still be open — deliberately added in Phase 8 to stop an
     idle system from falsely claiming containment — so once the breaker
     closed with no more traffic arriving, that check could never be
     satisfied again; the incident sat at `remediating` for its full 300s
     timeout, then failed, on a system that was by then completely
     healthy. Fixed by dropping the breaker-state requirement: two
     consecutive no-traffic checks now resolve any incident, since the
     Monitor's own re-alert path still catches a genuinely unresolved
     problem once traffic resumes.

   Both were invisible to every previous phase's live manual testing —
   they needed a long-running, unattended scenario with real timing to
   surface. After both fixes: both scenarios recover in ~58-60s, well
   inside NFR11's 120s budget (`tests/chaos/report.py`, a real run).

**5. `tests/chaos/report.py` is a measurement tool, not a pytest gate.**
The pytest tests in the same directory assert `< 120s`, pass/fail; the
report runs each scenario N times and prints an MTTR/recovery-rate
table — for when a real number needs quoting, not just a green check.

## Consequences
- **Accepted cost — chaos tests are slow** (~60-130s per scenario,
  network+real-timing bound by design) and need Docker running with the
  test override — never part of CI's fast path, which is exactly why
  they're excluded from `testpaths` rather than gated by a marker.
- **Known gap — only 2 of 10 failure scenarios have chaos coverage.**
  The rest still only have the design-level treatment in
  `07-failure-scenarios.md`.
- **Benefit:** proof, not assertion, that NFR11 holds — and a second,
  independent confirmation (after ADR-0015's chaos-found Healer bugs)
  that this system's failure scenarios need to be *run*, not just
  reasoned about, to find what's actually wrong with them.
- The `docker-compose.test.yml` override pattern is reusable for future
  scenarios needing direct infra access without touching the default
  `docker compose up` surface at all.

## Alternatives Considered
- **Measure MTTR purely from black-box HTTP polling (no DB access):**
  rejected — see decision 2; it can't distinguish "the system correctly
  contained the failure" from "the fault is still active," which is the
  entire point of measuring MTTR for an *automated remediation* system.
- **Publish Postgres/Redis by default:** rejected — real risk of
  colliding with a developer's own local Postgres/Redis on the standard
  ports; an explicit override file opted into only when needed is safer.
- **Cover all ten failure scenarios now:** rejected for this phase —
  the other eight don't yet have a concrete automated response to
  measure against; chaos-testing a response that doesn't exist yet
  would just be asserting silence.
