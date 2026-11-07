# ADR-0022: The Evaluation Harness Reuses Chaos Scenarios as Ground Truth

## Status
Accepted

## Context
Phase 15 needs "an incident evaluation dataset, detection/root-cause/
action accuracy, false positive/negative rates, prompt/version
tracking." This system has no real production incidents to build a
dataset from — but it does have something just as useful: Phase 11's
chaos scenarios already inject a *known, controlled* cause and already
measure MTTR from authoritative timestamps (ADR-0018). A chaos scenario
*is* a labeled incident — the fault that was injected is the ground
truth the Healer's diagnosis should have landed on.

Prompt/version tracking, meanwhile, already exists as data:
`agent_decisions.llm_model`/`llm_prompt_version` have been populated on
every Healer and Fraud Agent decision since Phase 8/9. What's missing
isn't the tracking mechanism — it's something that reads it back
against a known-correct answer.

## Decision

**1. The dataset (`tests/eval/dataset.py`) is five cases, not an
exhaustive matrix.** Two real chaos scenarios (provider outage, fraud-
service outage) with their correct `(action, service, dependency)`
label; one healthy control (no fault — the detection false-positive
check); one velocity-driven high-risk case and one low-risk control for
the Fraud Agent (the freeze/no-freeze false-negative/false-positive
check). Deliberately excludes the rules engine's ambiguous-evidence
escalation path (`agents/healer/rules.py`: zero or multiple implicated
dependencies) — already covered at the pure-logic level by
`tests/unit/test_healer.py`, and reproducing it live would need two
concurrent faults racing against Payment's own request spans, a path to
a flaky eval case rather than a more thorough one.

**2. Grading never asserts on the simulated geo signal.**
`agents/fraud/geo.py`'s anomaly is a deterministic function of a
transaction ID this dataset cannot predict in advance, and ADR-0019
already found that asserting on it directly is ~10% flaky. The honest
fix isn't a longer wait or a retry — it's grading on the one action
that actually matters (`freeze`), which geo alone can never trigger on
its own (it can only push a low-velocity case to `"borderline"`, and
`"borderline"` never freezes, by the design ADR-0016 already made).

**3. Fraud Agent cases are graded against User Service's real account
status, not the Fraud Agent's own logged decision.** The same "verify
the actual effect, not the self-report" discipline this project has
used for live verification since Phase 5 — an agent's own audit row
proves what it *believes* it did, not what actually happened to the
account.

**4. Grading (`tests/eval/grading.py`) is pure and unit-tested
independently of any live run.** No I/O — it takes the real rows a run
produced and compares them to the dataset's label. This mattered in
practice: a unit test caught a real bug before any live run did — the
first version of the "anomaly detected but no incident was ever opened"
branch marked that a detection false negative, which would have
conflated a Healer-responsiveness failure into Monitor's own
false-negative rate. Fixed to report it as a failed `action_correct`
instead, so the two failure modes stay distinguishable in the
aggregate. A live-only test strategy could easily have missed or
mis-attributed this.

**5. Prompt/version tracking is operationalized, not re-implemented.**
`tests/eval/db.py` reads back `llm_model`/`llm_prompt_version` from the
same `agent_decisions` rows Phase 8/9 already write; the report groups
by them. No new column, no new mechanism — Phase 15's job here was
making the existing data actually answerable ("did accuracy change
between prompt versions"), not inventing a parallel tracking system.

**6. The live runner (`tests/eval/report.py`) reuses the chaos
scenarios' own building blocks rather than duplicating them.**
`_drive_traffic`/`_wait_for_anomaly_and_incident` from
`tests/chaos/scenarios.py` are generalized to run off a case's fields
instead of two near-identical hardcoded functions — the same fault-
injection and polling logic, parameterized once.

## Consequences
- **Known gap — every live run so far exercised the rules fallback,
  never the LLM path.** No `ANTHROPIC_API_KEY` is configured locally;
  every `source` in the report reads `rules`. This is the same, honestly
  reported gap every prior phase involving the Healer or Fraud Agent's
  LLM path has carried (Phases 8, 9, 14) — the harness is built to
  report `llm` the moment a key is present, not to fake it now.
- **Known gap — a cosmetic Windows console-encoding artifact, not a data
  bug.** An existing Phase 11 message
  (`agents/healer/agent.py`'s `_verify()`: `"...no reproducing traffic —
  nothing currently observable to fail"`) contains an em dash that
  renders as a mojibake replacement character when this report's output
  is captured to a file on Windows without an explicit UTF-8 encoding.
  The underlying data in Postgres and the grading logic are both
  correct — confirmed by reading the row directly — this is purely a
  terminal/redirection encoding quirk, documented rather than "fixed" by
  changing an unrelated, working Phase 11 string.
- **Benefit — this is a real regression harness, not just a one-time
  report.** Verified live: 5/5 cases passed, 0 false positives, 0 false
  negatives, root-cause and action both correct for both chaos
  scenarios, both Fraud Agent cases matched their expected freeze
  outcome against real User Service state. The same run also exercised
  `tests/eval/db.py`'s JSONB query
  (`input_summary["user_id"].as_string()`) against real Postgres for
  the first time — it worked as written. Re-running this after any
  prompt, model, or rules change is now a single command
  (`python -m tests.eval.report`), not a manual judgment call.

## Alternatives Considered
- **A synthetic/hand-labeled incident dataset unrelated to the chaos
  scenarios:** rejected — would require inventing "incidents" with no
  real system behind them, when Phase 11 already produces real,
  controlled, labeled ones for free.
- **Reproducing the ambiguous-evidence escalation path live:** rejected
  — would need two concurrent faults racing against the same request
  spans to land reliably on "payment, more than one implicated
  dependency," a fragile setup for a case already proven correct at the
  unit level.
- **Grading the Fraud Agent on the `"borderline"` flag:** rejected —
  ADR-0019 already found this exact signal flaky in CI; grading only
  the freeze action sidesteps it without weakening what's actually
  being verified (the one outcome with a real consequence).
- **A new table or column for prompt/version comparison:** rejected —
  `agent_decisions.llm_model`/`llm_prompt_version` already exist and
  are already populated on every decision; adding another place to
  write the same fact would be a second source of truth to keep in
  sync, not a new capability.
