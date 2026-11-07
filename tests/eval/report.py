"""Phase 15 evaluation harness — runs the labeled dataset
(tests/eval/dataset.py) against the live stack, grades each run against
its ground truth (tests/eval/grading.py), and prints an accuracy report
grouped by case/source/prompt_version. Mirrors tests/chaos/report.py's
shape: a measurement tool, not a pytest gate.

Usage (from the repo root, test compose override already up):
    python -m tests.eval.report [--runs 3]
"""

import argparse
import asyncio
import time
import uuid

from tests.chaos.db import check_control_db_is_reachable, utcnow
from tests.chaos.scenarios import RESOLUTION_TIMEOUT_SECONDS, _drive_traffic, _wait_for_anomaly_and_incident
from tests.eval import db as eval_db
from tests.eval.dataset import FRAUD_CASES, HEALER_CASES, FraudEvalCase, HealerEvalCase
from tests.eval.grading import CaseResult, grade_fraud_case, grade_healer_case
from tests.live_helpers import (
    StackUnavailable,
    check_stack_is_up,
    clear_fault,
    create_funded_user,
    enable_fault,
    get_user,
    login,
    make_payment,
)

# Comfortably longer than Monitor's default window (60s) + poll interval
# (15s), so a quiet period here means the window genuinely saw nothing —
# not that the Monitor simply hadn't polled yet.
CONTROL_WAIT_SECONDS = 90
FRAUD_WAIT_SECONDS = 30


async def run_healer_case(token: str, case: HealerEvalCase) -> CaseResult:
    user_id = create_funded_user(token, "1000.00")
    started_at = utcnow()

    if case.fault_target:
        enable_fault(
            token,
            case.fault_target,
            case.fault_mode,
            error_rate=case.fault_error_rate,
            duration_seconds=case.fault_duration_seconds,
        )
    try:
        _drive_traffic(token, user_id, count=case.traffic_count)
        if case.expected_anomaly is not None:
            service, metric = case.expected_anomaly
            anomaly, incident = await _wait_for_anomaly_and_incident(
                service, metric, started_at, RESOLUTION_TIMEOUT_SECONDS
            )
        else:
            anomaly = await eval_db.any_anomaly_after(started_at)
            if anomaly is None:
                await asyncio.sleep(CONTROL_WAIT_SECONDS)
                anomaly = await eval_db.any_anomaly_after(started_at)
            incident = None
    finally:
        if case.fault_target:
            clear_fault(token, case.fault_target)

    decision = await eval_db.decision_for_incident(incident.id) if incident is not None else None
    return grade_healer_case(case, anomaly, incident, decision)


async def run_fraud_case(token: str, case: FraudEvalCase) -> CaseResult:
    user_id = create_funded_user(token, "10000.00")
    started_at = utcnow()
    for _ in range(case.payment_count):
        make_payment(token, user_id, "1.00", idempotency_key=f"eval-{uuid.uuid4().hex}")

    # The ground truth is the account's actual status, not the agent's own
    # logged claim about itself — same "verify the real effect" discipline
    # as every other live test in this project.
    status = "active"
    deadline = time.monotonic() + FRAUD_WAIT_SECONDS
    while time.monotonic() < deadline:
        status = get_user(token, user_id)["status"]
        if status == "frozen":
            break
        await asyncio.sleep(2)

    decision = await eval_db.latest_fraud_decision_after(uuid.UUID(user_id), started_at)
    return grade_fraud_case(case, status, decision)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="runs per case (default 1)")
    args = parser.parse_args()

    try:
        check_stack_is_up()
        await check_control_db_is_reachable()
    except StackUnavailable as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - same actionable hint as tests/chaos/report.py
        raise SystemExit(
            "flowguard_control isn't reachable on localhost:5432 — run with the test compose "
            "override: docker compose -f docker-compose.yml -f "
            f"infra/docker/docker-compose.test.yml up --build -d ({exc})"
        ) from exc

    token = login()
    results: list[CaseResult] = []

    for run in range(args.runs):
        for case in HEALER_CASES:
            print(f"[run {run + 1}/{args.runs}] healer/{case.name}...", flush=True)
            results.append(await run_healer_case(token, case))
        for case in FRAUD_CASES:
            print(f"[run {run + 1}/{args.runs}] fraud/{case.name}...", flush=True)
            results.append(await run_fraud_case(token, case))

    print("\n" + render_report(results))


def render_report(results: list[CaseResult]) -> str:
    header = (
        f"{'case':<22} {'result':<6} {'fp':<5} {'fn':<5} {'root_cause':<10} "
        f"{'action':<6} {'source':<6} {'prompt_version':<16} detail"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.case:<22} {'PASS' if r.passed else 'FAIL':<6} {str(r.false_positive):<5} "
            f"{str(r.false_negative):<5} {str(r.root_cause_correct):<10} {str(r.action_correct):<6} "
            f"{str(r.source):<6} {str(r.prompt_version):<16} {r.detail}"
        )

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    false_positives = sum(1 for r in results if r.false_positive)
    false_negatives = sum(1 for r in results if r.false_negative)
    lines.append("")
    if total:
        lines.append(
            f"Overall: {passed}/{total} passed ({passed / total * 100:.0f}%); "
            f"{false_positives} false positive(s), {false_negatives} false negative(s)"
        )
    else:
        lines.append("No results.")
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())
