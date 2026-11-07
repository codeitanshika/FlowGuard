"""Pure grading logic — no I/O, no live stack, so correctness here is
verified by ordinary unit tests (tests/unit/test_eval_grading.py) rather
than only by a live run. tests/eval/report.py supplies the real rows;
this module only compares them against tests/eval/dataset.py's labels."""

import re
from dataclasses import dataclass

from shared.control_plane import AgentDecision, Anomaly, Incident
from tests.eval.dataset import FraudEvalCase, HealerEvalCase

_ACTION_TAKEN_RE = re.compile(r"^([a-z-]+):([a-z_]+):([a-z_]+)")


def _decision_source(decision: AgentDecision | None) -> str | None:
    if decision is None:
        return None
    return "llm" if decision.llm_model else "rules"


@dataclass(frozen=True)
class CaseResult:
    case: str
    false_positive: bool
    false_negative: bool
    root_cause_correct: bool | None  # None when not applicable (no anomaly either way)
    action_correct: bool | None
    source: str | None  # 'rules' | 'llm' | None (no decision recorded)
    prompt_version: str | None
    detail: str

    @property
    def passed(self) -> bool:
        return (
            not self.false_positive
            and not self.false_negative
            and self.root_cause_correct is not False
            and self.action_correct is not False
        )


def parse_action_taken(action_taken: str | None) -> tuple[str, str, str] | None:
    """incidents.action_taken is built in agents/healer/agent.py as
    f"{action}:{service}:{dependency}", optionally with a trailing
    explanatory suffix (" (already applied by an earlier action)") —
    matched as a prefix so that suffix doesn't break the parse."""
    if not action_taken:
        return None
    match = _ACTION_TAKEN_RE.match(action_taken)
    return (match.group(1), match.group(2), match.group(3)) if match else None


def grade_healer_case(
    case: HealerEvalCase,
    anomaly: Anomaly | None,
    incident: Incident | None,
    decision: AgentDecision | None,
) -> CaseResult:
    source = _decision_source(decision)
    prompt_version = decision.llm_prompt_version if decision else None

    if case.expected_anomaly is None:
        # The false-positive control: no anomaly should fire at all.
        false_positive = anomaly is not None
        detail = (
            f"unexpected anomaly: {anomaly.service}/{anomaly.metric}" if false_positive else "no anomaly, as expected"
        )
        return CaseResult(case.name, false_positive, False, None, None, source, prompt_version, detail)

    if anomaly is None:
        return CaseResult(
            case.name, False, True, None, None, None, None, f"expected {case.expected_anomaly}, none detected"
        )

    detected_correct = (anomaly.service, anomaly.metric) == case.expected_anomaly
    if incident is None:
        # Detection worked — this is not a false negative, Monitor did its
        # job. The Healer never picking the anomaly up is a different,
        # real failure (action_correct=False, not None/"not applicable"),
        # and the two must not be conflated in the aggregate FN rate.
        return CaseResult(
            case.name,
            False,
            False,
            detected_correct,
            False,
            source,
            prompt_version,
            "anomaly detected but no incident was ever opened for it",
        )

    actual_action = parse_action_taken(incident.action_taken)
    action_correct = actual_action == case.expected_action if case.expected_action else actual_action is None
    root_cause_correct = detected_correct  # the anomaly IS the detected root cause's locus here

    return CaseResult(
        case.name,
        False,
        False,
        root_cause_correct,
        action_correct,
        source,
        prompt_version,
        f"anomaly={anomaly.service}/{anomaly.metric} action_taken={incident.action_taken!r}",
    )


def grade_fraud_case(case: FraudEvalCase, actual_status: str, decision: AgentDecision | None) -> CaseResult:
    source = _decision_source(decision)
    prompt_version = decision.llm_prompt_version if decision else None
    froze = actual_status == "frozen"

    action_correct = froze == case.expect_freeze
    detail = f"expected_freeze={case.expect_freeze} actual_status={actual_status!r}"
    # "false positive" / "false negative" here are about the one action
    # that matters (freeze), not about the Fraud Agent's "borderline" flag
    # — see dataset.py's docstring for why that's the honest boundary.
    false_positive = froze and not case.expect_freeze
    false_negative = (not froze) and case.expect_freeze

    return CaseResult(case.name, false_positive, false_negative, None, action_correct, source, prompt_version, detail)
