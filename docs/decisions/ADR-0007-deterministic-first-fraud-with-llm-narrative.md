# ADR-0007: Deterministic-First Fraud Scoring; LLM Supplies Narrative, Not the Decision

## Status
Accepted

## Context
Freezing a user's account is a high-consequence, hard-to-reverse action
from the user's perspective. An LLM is genuinely useful for producing a
human-readable rationale for *why* a pattern looks suspicious, and for
weighing ambiguous, borderline signals the way a human fraud analyst
would. But letting an LLM be the sole authority over whether an account
gets frozen means a hallucination or an unusual-but-legitimate spending
pattern could lock out a real user with no deterministic safety net.

## Decision
The freeze/no-freeze decision is always made by deterministic rules
(transaction velocity thresholds, geo-anomaly checks) evaluated against
Redis sliding-window data. The LLM is only invoked for transactions that
fall in a **borderline band** the deterministic rules can't confidently
resolve either way, and even then its output is constrained to a
rationale/confidence signal that a human or the audit log reads — it is
never given direct write authority over `users.status`. The actual mutation
still goes through User Service's freeze endpoint, called with the
deterministic decision as the reason of record.

## Consequences
- **Accepted cost:** some genuinely ambiguous fraud cases that a more
  context-aware LLM judgment might catch will fall on the conservative
  side of a fixed threshold instead. This is a deliberate trade of recall
  for predictability and auditability.
- **Benefit:** the freeze decision is always explainable by a fixed rule
  ("14 transactions in 3 minutes, threshold is 10"), which is testable,
  reproducible, and immune to LLM output variance — critical for something
  users can dispute. The LLM's contribution (narrative/confidence) is
  additive, not load-bearing.
- **Where the LLM does add value:** the human-readable explanation
  attached to a freeze event, and flagging borderline cases for a
  narrative that a human reviewer can act on faster than raw numbers.

## Alternatives Considered
- **LLM-only risk assessment:** rejected — no deterministic fallback if
  the LLM is unavailable or produces a low-confidence/malformed response
  (see [07-failure-scenarios.md](../architecture/07-failure-scenarios.md),
  scenario 8), and no way to audit *why* a specific freeze happened beyond
  "the model said so."
- **No LLM involvement at all:** considered, but rejected — it would
  remove the one place in the Fraud Agent where genuinely valuable
  reasoning (narrative generation, weighing borderline ambiguity) belongs,
  and this project's goal explicitly includes demonstrating LLM reasoning
  used *appropriately*, not avoided entirely.
