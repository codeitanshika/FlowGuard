# ADR-0005: LLM Decisions Execute Only Through an Allowlisted Ops Controller

## Status
Accepted — this is the most safety-critical decision in the system.

## Context
The Healer Agent uses an LLM to diagnose ambiguous incidents and propose a
recovery action. If that LLM output were ever allowed to reach a shell,
a Docker command, or an arbitrary service endpoint, a hallucinated or
adversarially-influenced response could take a destructive, unbounded
action — the exact opposite of what a "self-healing" system should do.
Prompt-level instructions ("never run destructive commands") are not a
safety boundary; they are a request the model can fail to honor.

## Decision
Introduce **Ops Controller** as its own component — the only part of the
system with credentials to call container restart operations or another
service's internal circuit-breaker endpoints. It exposes a fixed, code-defined
allowlist of named operations (`open-circuit`, `reset-circuit`,
`shed-traffic`, `restart-service`), each with a strict parameter schema.

The path from LLM output to real-world effect is:
```
LLM free-form reasoning
  → structured decision (HealerDecision pydantic schema)
  → schema validation (reject anything that doesn't parse)
  → allowlist lookup by action name (reject anything not on the list)
  → Ops Controller call with only the validated, allowlisted parameters
  → Ops Controller's own executor (not the LLM's text) performs the action
  → every call — accepted or rejected — is written to an audit log first
```

The LLM never sees a shell, a Docker socket, or raw infrastructure
credentials. It only ever produces data; a fixed piece of non-LLM code
decides whether that data corresponds to something the system is willing
to do.

## Consequences
- **Accepted cost:** every new kind of recovery action requires a
  deliberate code change to add it to the allowlist — the system cannot
  "invent" a new capability at runtime, even if that would sometimes be
  the genuinely correct fix. This is the point, not a limitation to work
  around.
- **Benefit:** a full class of failure modes (prompt injection via
  malicious telemetry, hallucinated commands, model drift) is architecturally
  incapable of causing destructive action, because the action space is
  closed and enumerated in code, independent of model behavior.
- **Auditability:** because every call — including rejected ones — is
  logged before execution, Phase 15's AI-safety evaluation has a complete,
  trustworthy record of what the model proposed versus what actually ran.

## Alternatives Considered
- **Give the Healer Agent direct access to a Docker SDK/shell, with prompt
  instructions to be careful:** rejected outright — this is exactly the
  pattern this ADR exists to prevent.
- **Human-in-the-loop approval for every action:** rejected as the default
  — it would defeat the goal of automated recovery with a meaningful MTTR
  (NFR11). Kept as a conceptual fallback: an action outside the allowlist
  results in an unresolved incident surfaced for manual review, not a
  blocked pipeline waiting on a human for every routine breaker trip.
- **Let the LLM call service APIs "directly" but rate-limited:** rejected
  — rate limiting bounds *frequency*, not *scope*; it doesn't stop a single
  bad call from doing damage.
