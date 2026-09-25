"""Chaos test for failure scenario #2 — measures real MTTR against NFR11.
See test_provider_outage_recovery.py for how to run this suite."""

from tests.chaos.scenarios import RESOLUTION_TIMEOUT_SECONDS, run_fraud_service_outage_scenario


async def test_fraud_service_outage_recovers_within_nfr11_budget(merchant_token):
    result = await run_fraud_service_outage_scenario(merchant_token)

    assert result.recovered, (
        f"scenario did not resolve within {RESOLUTION_TIMEOUT_SECONDS}s: {result.detail}"
    )
    assert result.resolve_seconds is not None and result.resolve_seconds < 120, (
        f"NFR11 requires MTTR < 120s in the local/demo environment; got "
        f"{result.resolve_seconds:.1f}s ({result.detail})"
    )
