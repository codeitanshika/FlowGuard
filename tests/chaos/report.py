"""Standalone MTTR / recovery-rate measurement (Phase 11) — runs each
chaos scenario N times against the live stack and prints a report. This
is a measurement tool, not a pass/fail gate (that's what the pytest tests
in this directory are for); run it when you want an actual number to
quote, not just a green check.

Usage (from the repo root, test compose override already up):
    python -m tests.chaos.report [--runs 3]
"""

import argparse
import asyncio
import statistics

from tests.chaos.db import check_control_db_is_reachable
from tests.chaos.scenarios import ChaosResult, run_fraud_service_outage_scenario, run_provider_outage_scenario
from tests.live_helpers import StackUnavailable, check_stack_is_up, login

SCENARIOS = {
    "provider_outage": run_provider_outage_scenario,
    "fraud_service_outage": run_fraud_service_outage_scenario,
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="runs per scenario (default 3)")
    args = parser.parse_args()

    try:
        check_stack_is_up()
        await check_control_db_is_reachable()
    except StackUnavailable as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - report the same actionable hint as the pytest fixtures
        raise SystemExit(
            "flowguard_control isn't reachable on localhost:5432 — run with the test compose "
            "override: docker compose -f docker-compose.yml -f "
            f"infra/docker/docker-compose.test.yml up --build -d ({exc})"
        ) from exc

    token = login()
    results: list[ChaosResult] = []

    print(f"Running each scenario {args.runs}x against the live stack — this takes a few minutes.\n")
    for name, run_scenario in SCENARIOS.items():
        for i in range(args.runs):
            print(f"[{name}] run {i + 1}/{args.runs}...", flush=True)
            result = await run_scenario(token)
            results.append(result)
            status = "recovered" if result.recovered else "DID NOT RECOVER"
            print(f"  -> {status}: {result.detail}")

    print("\n" + _render_table(results))
    print("\n" + _render_summary(results))


def _render_table(results: list[ChaosResult]) -> str:
    header = f"{'scenario':<22} {'run':>4} {'detected(s)':>12} {'resolved(s)':>12} {'outcome':<12}"
    lines = [header, "-" * len(header)]
    counts: dict[str, int] = {}
    for r in results:
        counts[r.scenario] = counts.get(r.scenario, 0) + 1
        detect = f"{r.detect_seconds:.1f}" if r.detect_seconds is not None else "-"
        resolve = f"{r.resolve_seconds:.1f}" if r.resolve_seconds is not None else "-"
        lines.append(f"{r.scenario:<22} {counts[r.scenario]:>4} {detect:>12} {resolve:>12} {r.outcome or 'timeout':<12}")
    return "\n".join(lines)


def _render_summary(results: list[ChaosResult]) -> str:
    lines = ["Summary (NFR11 target: MTTR < 120s):"]
    by_scenario: dict[str, list[ChaosResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario, []).append(r)

    for name, group in by_scenario.items():
        recovered = [r for r in group if r.recovered and r.resolve_seconds is not None]
        recovery_rate = len(recovered) / len(group) * 100
        mttr = f"{statistics.mean(r.resolve_seconds for r in recovered):.1f}s" if recovered else "n/a"
        within_budget = sum(1 for r in recovered if r.resolve_seconds < 120)
        lines.append(
            f"  {name}: {len(recovered)}/{len(group)} recovered ({recovery_rate:.0f}%), "
            f"mean MTTR {mttr}, {within_budget}/{len(recovered)} within the 120s NFR11 budget"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())
