# ADR-0016: Fraud Agent — Simulated Geo Signal, Borderline Never Freezes, Freeze Cooldown

## Status
Accepted. Implements [ADR-0007](ADR-0007-deterministic-first-fraud-with-llm-narrative.md).

## Context
ADR-0007 already settled the core principle for the Fraud Agent: the
freeze decision is always deterministic, the LLM only ever supplies a
narrative/confidence signal. Building the agent (Phase 9) required three
further decisions the Phase 0 design left open.

## Decision

**1. The geo-anomaly signal is simulated, deterministically, with no
state.** `payment.created` carries no IP or location — there is no real
geolocation data anywhere in this system, and building one is out of
scope. `agents/fraud/geo.py` derives a "home country" for a user and an
"observed country" for a transaction purely from `hashlib.sha256` of
their ids (not Python's built-in `hash()`, which is randomized per
process unless `PYTHONHASHSEED` is fixed — that would make a user's home
country change across Fraud Agent restarts). About 1 in
`geo_mismatch_denominator` (default 10) transactions are simulated as a
mismatch. This needs no Redis state at all: the "signal" is a pure
function, reproducible for any given transaction id, which makes it easy
to write a fixed test case or reconstruct exactly what a specific past
decision saw.

**2. Borderline never freezes — it is only ever logged for human
review.** The Phase 0 event-flow doc's step 5 ("if the *combined*
deterministic decision is high risk, freeze") could be read as the LLM's
confidence nudging a borderline case into a freeze. That reading is
rejected: it would give the LLM real, if indirect, authority over
`users.status`, which is exactly what ADR-0007 forbids. Concretely, the
LLM's output schema (`FraudNarrative`) has no field that could express a
decision at all — narrowing the failure mode from "the code ignores an
action field" to "there is no action field to ignore." A borderline
verdict always produces an `agent_decisions` row with `executed=false`
and never calls User Service's freeze endpoint, regardless of the
narrative's stated confidence — proven by a test that gives the narrator
a "high confidence" response and asserts nothing about the outcome
changes.

**3. A freeze cooldown per user.** User Service's freeze endpoint is
idempotent on `users.status` but not idempotent in its side effects: every
call writes a `freeze_events` row and touches `updated_at`
(`services/user/app/db/repository.py`). A user who keeps transacting
after crossing the velocity threshold would otherwise produce one freeze
call — and one audit row — per subsequent transaction. A Redis key
(`fraud:freeze_cooldown:{user_id}`, `SET NX EX`, default 300s) bounds
this to one freeze call per user per cooldown window; a failed freeze
call releases the key immediately so the very next high-risk event
retries rather than waiting out the full cooldown for nothing. Verified
live: 3 consecutive high-velocity payments for one user produced exactly
1 `freeze_events` row, not 3.

## Consequences
- **Accepted cost — the geo signal is not a real fraud signal.** It
  demonstrates the code path (a second, independent deterministic input
  to `risk.decide()`) and nothing about actual geographic risk. Replacing
  it with real data is future work, not attempted here.
- **Accepted cost — some genuinely high-risk borderline cases go
  unfrozen until a human acts**, or until later events push the same
  user's velocity count over the freeze threshold on their own. This is
  the same recall-for-predictability trade ADR-0007 already accepted,
  made concrete here.
- **Benefit:** decision 2 makes ADR-0007's guarantee independently
  verifiable by reading one file (`schemas.py`) rather than by auditing
  every call site that touches a `HealerDecision`-like object.
- Phase 14/15 can query `agent_decisions` (`agent = 'fraud_agent'`) the
  same way it queries the Healer's rows, since both write to the same
  table with the same shape.

## Alternatives Considered
- **Persist a per-user home country in Redis on first sight, instead of
  hashing it fresh each time:** rejected — it adds state and a cold-start
  edge case (what's "home" on transaction 1?) for no behavioral
  difference; the hash-based version is already stable per user for free.
- **Let a high-confidence LLM narrative escalate a borderline case to
  high:** rejected outright as a violation of ADR-0007, not seriously
  considered as a design option.
- **No cooldown; let User Service's own idempotency absorb repeats:**
  rejected — "idempotent on status" is not "free of side effects", and an
  attack pattern that keeps transacting after the threshold would flood
  `freeze_events` and the Fraud Agent's own audit table.
