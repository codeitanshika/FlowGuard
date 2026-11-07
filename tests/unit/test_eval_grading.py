import uuid

from shared.control_plane import AgentDecision, Anomaly, AnomalySeverity, Incident, IncidentStatus
from tests.eval.dataset import FraudEvalCase, HealerEvalCase
from tests.eval.grading import grade_fraud_case, grade_healer_case, parse_action_taken

PROVIDER_CASE = HealerEvalCase(
    name="provider_outage",
    fault_target="payment-provider",
    fault_mode="error_500",
    fault_error_rate=1.0,
    fault_duration_seconds=90,
    traffic_count=15,
    expected_anomaly=("payment", "error_rate"),
    expected_action=("open-circuit", "payment", "provider"),
)

CONTROL_CASE = HealerEvalCase(
    name="healthy_control",
    fault_target="",
    fault_mode="none",
    fault_error_rate=0.0,
    fault_duration_seconds=0,
    traffic_count=15,
    expected_anomaly=None,
    expected_action=None,
)


def _anomaly(service="payment", metric="error_rate") -> Anomaly:
    return Anomaly(
        id=uuid.uuid4(),
        service=service,
        metric=metric,
        observed_value=1.0,
        threshold=0.2,
        severity=AnomalySeverity.critical,
        trace_id=None,
    )


def _incident(action_taken: str | None, status=IncidentStatus.resolved) -> Incident:
    return Incident(id=uuid.uuid4(), anomaly_id=uuid.uuid4(), status=status, action_taken=action_taken)


def _decision(llm_model: str | None = None, prompt_version: str | None = None) -> AgentDecision:
    return AgentDecision(
        id=uuid.uuid4(),
        agent="healer",
        input_summary={},
        decision={},
        validated=True,
        executed=True,
        llm_model=llm_model,
        llm_prompt_version=prompt_version,
    )


def test_parse_action_taken_strips_trailing_explanation():
    assert parse_action_taken("open-circuit:payment:provider") == ("open-circuit", "payment", "provider")
    assert parse_action_taken("open-circuit:payment:fraud (already applied by an earlier action)") == (
        "open-circuit",
        "payment",
        "fraud",
    )


def test_parse_action_taken_returns_none_for_escalation_or_noise():
    assert parse_action_taken("escalated: ambiguous evidence") is None
    assert parse_action_taken(None) is None


def test_correct_detection_and_action_passes():
    result = grade_healer_case(PROVIDER_CASE, _anomaly(), _incident("open-circuit:payment:provider"), _decision())
    assert result.passed
    assert result.root_cause_correct is True
    assert result.action_correct is True
    assert result.source == "rules"


def test_llm_sourced_decision_is_reported_as_llm():
    result = grade_healer_case(
        PROVIDER_CASE,
        _anomaly(),
        _incident("open-circuit:payment:provider"),
        _decision(llm_model="claude-opus-5", prompt_version="healer-v1"),
    )
    assert result.source == "llm"
    assert result.prompt_version == "healer-v1"


def test_wrong_action_is_not_a_false_positive_but_fails_action_grading():
    result = grade_healer_case(PROVIDER_CASE, _anomaly(), _incident("open-circuit:payment:fraud"), _decision())
    assert not result.false_positive
    assert not result.false_negative
    assert result.action_correct is False
    assert not result.passed


def test_missing_anomaly_is_a_false_negative():
    result = grade_healer_case(PROVIDER_CASE, None, None, None)
    assert result.false_negative
    assert not result.passed


def test_detected_but_never_actioned_fails_without_being_a_false_negative():
    result = grade_healer_case(PROVIDER_CASE, _anomaly(), None, None)
    assert not result.false_negative  # detection worked; this is a different failure
    assert result.root_cause_correct is True
    assert result.action_correct is False
    assert not result.passed


def test_control_case_passes_with_no_anomaly():
    result = grade_healer_case(CONTROL_CASE, None, None, None)
    assert not result.false_positive
    assert result.passed


def test_control_case_with_unexpected_anomaly_is_a_false_positive():
    result = grade_healer_case(CONTROL_CASE, _anomaly("gateway", "p95_latency"), None, None)
    assert result.false_positive
    assert not result.passed


FRAUD_HIGH = FraudEvalCase(name="fraud_velocity_high", payment_count=10, expect_freeze=True)
FRAUD_LOW = FraudEvalCase(name="fraud_low_control", payment_count=1, expect_freeze=False)


def test_fraud_high_risk_freeze_passes():
    result = grade_fraud_case(FRAUD_HIGH, "frozen", _decision())
    assert result.action_correct is True
    assert result.passed


def test_fraud_high_risk_not_frozen_is_a_false_negative():
    result = grade_fraud_case(FRAUD_HIGH, "active", None)
    assert result.false_negative
    assert not result.passed


def test_fraud_low_control_stays_active():
    result = grade_fraud_case(FRAUD_LOW, "active", None)
    assert result.passed
    assert result.source is None


def test_fraud_low_control_unexpectedly_frozen_is_a_false_positive():
    result = grade_fraud_case(FRAUD_LOW, "frozen", _decision())
    assert result.false_positive
    assert not result.passed
