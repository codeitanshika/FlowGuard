"""The Phase 15 incident evaluation dataset — ground-truth labels for what
the Healer and Fraud Agent *should* decide, reused every time this runs
(tests/eval/report.py). Each case names a known, controlled cause (a real
fault, or a known transaction pattern) and the one correct outcome for it,
so grading is a comparison against a fixed label, not a guess about what
"good" looks like.

Deliberately excludes the rules engine's own ambiguous-evidence escalation
path (agents/healer/rules.py: more than one implicated dependency, or
none at all) — that is already covered by tests/unit/test_healer.py at
the pure-logic level, and reproducing it live would need two concurrent
faults racing against Payment's own request spans, which is a path to a
flaky eval case, not a more thorough one.

Only two things are genuinely controlled per Fraud Agent case: the
*freeze* action (only "high" ever freezes — "borderline" deliberately
never does, per ADR-0016) and velocity_count. The simulated geo signal
(agents/fraud/geo.py) is NOT controlled here — it's a function of a
transaction_id this dataset cannot predict in advance, and CI already
found that asserting on it directly is flaky (ADR-0019). Grading only
ever checks the freeze action, which geo_anomaly alone can never trigger
(it can only push a low-velocity case to "borderline", and borderline
never freezes) — see tests/eval/grading.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HealerEvalCase:
    name: str
    fault_target: str  # Gateway fault-injection target
    fault_mode: str
    fault_error_rate: float
    fault_duration_seconds: int
    traffic_count: int
    # (service, metric) the Monitor should detect, or None if this case
    # expects no anomaly at all (the false-positive control).
    expected_anomaly: tuple[str, str] | None
    # (action, service, dependency) the Healer should act on, or None if
    # this case expects no anomaly in the first place.
    expected_action: tuple[str, str, str] | None


@dataclass(frozen=True)
class FraudEvalCase:
    name: str
    payment_count: int
    expect_freeze: bool


HEALER_CASES: list[HealerEvalCase] = [
    HealerEvalCase(
        name="provider_outage",
        fault_target="payment-provider",
        fault_mode="error_500",
        fault_error_rate=1.0,
        fault_duration_seconds=90,
        traffic_count=15,
        expected_anomaly=("payment", "error_rate"),
        expected_action=("open-circuit", "payment", "provider"),
    ),
    HealerEvalCase(
        name="fraud_service_outage",
        fault_target="fraud",
        fault_mode="error_500",
        fault_error_rate=0.4,
        fault_duration_seconds=90,
        traffic_count=25,
        expected_anomaly=("fraud", "error_rate"),
        expected_action=("open-circuit", "payment", "fraud"),
    ),
    HealerEvalCase(
        # The false-positive control: normal traffic, no fault. Default
        # thresholds (error_rate_warning=0.05, min_throughput_rps unset)
        # mean a healthy run should raise nothing — see
        # agents/monitor/thresholds.py.
        name="healthy_control",
        fault_target="",
        fault_mode="none",
        fault_error_rate=0.0,
        fault_duration_seconds=0,
        traffic_count=15,
        expected_anomaly=None,
        expected_action=None,
    ),
]

FRAUD_CASES: list[FraudEvalCase] = [
    FraudEvalCase(
        # thresholds.high defaults to 10 (agents/fraud/thresholds.py) —
        # velocity alone forces level="high" regardless of the simulated
        # geo signal's incidental value for these transactions.
        name="fraud_velocity_high",
        payment_count=10,
        expect_freeze=True,
    ),
    FraudEvalCase(
        # The false-positive control for the freeze action specifically
        # (not for the "borderline" flag, which the geo signal can
        # legitimately and harmlessly set — see this module's docstring).
        name="fraud_low_control",
        payment_count=1,
        expect_freeze=False,
    ),
]
