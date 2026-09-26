import asyncio
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from agents.healer import db
from agents.healer.allowlist import Plan, plan
from agents.healer.config import Settings
from agents.healer.context import ContextGatherer
from agents.healer.diagnosis import PROMPT_VERSION, Diagnoser
from agents.healer.ops_client import OpsClient, OpsError
from agents.healer.schemas import AnomalyEvent
from agents.healer.verifier import check_recovery, check_still_failing
from agents.monitor.metrics_client import JaegerMetricsClient, MetricsUnavailableError
from agents.monitor.thresholds import resolve_thresholds
from agents.ops_controller.allowlist import describe_allowlist
from shared.control_plane import IncidentStatus
from shared.events import Channels, RedisEventBus, consume_forever
from shared.logging import get_logger

logger = get_logger(__name__)


class HealerAgent:
    """anomaly.detected -> diagnose -> validate -> (Ops Controller) -> verify
    -> incident.resolved. Every incident is worked by its own task so a
    five-minute verification never blocks the next anomaly, and every
    incident is guaranteed to end in a terminal state."""

    def __init__(
        self,
        settings: Settings,
        bus: RedisEventBus,
        gatherer: ContextGatherer,
        diagnoser: Diagnoser,
        ops: OpsClient,
        metrics: JaegerMetricsClient,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._gatherer = gatherer
        self._diagnoser = diagnoser
        self._ops = ops
        self._metrics = metrics
        self._tasks: set[asyncio.Task] = set()

    # --- intake -----------------------------------------------------------

    async def run_forever(self) -> None:
        await consume_forever(self._bus, Channels.ANOMALY_DETECTED, on_message=self._on_message, service_name="healer")

    async def _on_message(self, message: dict) -> None:
        try:
            anomaly = AnomalyEvent.model_validate_json(message["data"])
        except ValueError as exc:
            # Poison event (failure scenario 10): log and drop, never crash.
            logger.error("event.invalid", error=str(exc)[:300])
            return
        self._spawn(anomaly)

    def _spawn(self, anomaly: AnomalyEvent) -> None:
        overloaded = len(self._tasks) >= self._settings.max_concurrent_incidents
        coro = self._reject_overloaded(anomaly) if overloaded else self._handle(anomaly)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("healer.incident_task_crashed", error=str(task.exception()))

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with suppress(asyncio.CancelledError):
                await task

    async def _reject_overloaded(self, anomaly: AnomalyEvent) -> None:
        incident = await db.create_incident(anomaly.anomaly_id)
        logger.error("healer.overloaded", anomaly_id=str(anomaly.anomaly_id))
        await self._close(
            incident.id,
            anomaly.anomaly_id,
            "failed",
            None,
            "not handled: Healer at max concurrent incidents",
        )

    # --- one incident -----------------------------------------------------

    async def _handle(self, anomaly: AnomalyEvent) -> None:
        incident = await db.create_incident(anomaly.anomaly_id)
        incident_id = incident.id
        logger.info(
            "healer.incident_opened",
            incident_id=str(incident_id),
            anomaly_id=str(anomaly.anomaly_id),
            service=anomaly.service,
            metric=anomaly.metric,
            severity=anomaly.severity,
        )
        await self._publish(
            Channels.INCIDENT_DIAGNOSING,
            incident_id=str(incident_id),
            anomaly_id=str(anomaly.anomaly_id),
            service=anomaly.service,
            metric=anomaly.metric,
        )

        root_cause: str | None = None
        try:
            facts = await self._gatherer.trace_facts(anomaly.trace_id)
            circuits = await self._gatherer.circuits()
            diagnosis = await self._diagnoser.diagnose(anomaly, facts, circuits, await self._allowed_actions())
            decision = diagnosis.decision
            root_cause = decision.root_cause
            chosen = plan(decision, circuits)

            decision_id = await db.record_decision(
                incident_id,
                input_summary={
                    "anomaly": {
                        "service": anomaly.service,
                        "metric": anomaly.metric,
                        "observed_value": anomaly.observed_value,
                        "threshold": anomaly.threshold,
                        "severity": anomaly.severity,
                    },
                    "evidence_spans": len(facts),
                    "circuit_breakers": circuits,
                    "source": diagnosis.source,
                    "fallback_reason": diagnosis.fallback_reason,
                },
                decision={
                    "proposed": decision.model_dump(),
                    "plan": {"kind": chosen.kind, "reason": chosen.reason},
                },
                validated=chosen.validated,
                executed=False,
                llm_model=diagnosis.llm_model,
                llm_prompt_version=PROMPT_VERSION if diagnosis.source == "llm" else None,
                llm_latency_ms=diagnosis.latency_ms,
                llm_tokens_in=diagnosis.tokens_in,
                llm_tokens_out=diagnosis.tokens_out,
            )
            logger.info(
                "healer.decision",
                incident_id=str(incident_id),
                source=diagnosis.source,
                proposed=decision.action,
                plan=chosen.kind,
                reason=chosen.reason,
            )

            if chosen.kind == "escalate":
                await self._close(
                    incident_id, anomaly.anomaly_id, "escalated", root_cause, f"escalated: {chosen.reason}"
                )
                return

            action_taken = f"{chosen.action}:{chosen.service}:{chosen.dependency}"
            if chosen.kind == "execute" and chosen.action == "open-circuit":
                stale = await self._stale_reason(anomaly)
                if stale:
                    await self._close(
                        incident_id,
                        anomaly.anomaly_id,
                        "resolved",
                        root_cause,
                        f"no action taken: {stale}",
                    )
                    return
            if chosen.kind == "execute":
                await db.update_incident(incident_id, IncidentStatus.remediating, root_cause)
                try:
                    result = await self._ops.execute(chosen.action, chosen.service, chosen.dependency)
                except OpsError as exc:
                    if exc.status != 429:
                        await self._close(
                            incident_id,
                            anomaly.anomaly_id,
                            "failed",
                            root_cause,
                            f"{action_taken} not applied: {exc}",
                        )
                        return
                    # The cooldown says the action ran recently, not that its
                    # effect still holds (something may have reset the
                    # breaker since) — so check the actual state.
                    wanted = "open" if chosen.action == "open-circuit" else "closed"
                    current = ((await self._gatherer.circuits()) or {}).get(chosen.dependency)
                    if current != wanted:
                        await self._close(
                            incident_id,
                            anomaly.anomaly_id,
                            "failed",
                            root_cause,
                            f"{action_taken} blocked by the Ops cooldown and the breaker is "
                            f"'{current}', not '{wanted}'; a later anomaly will retry",
                        )
                        return
                    action_taken += " (already applied by an earlier action)"
                else:
                    await db.mark_decision_executed(decision_id)
                    logger.warning(
                        "healer.action_executed",
                        incident_id=str(incident_id),
                        action=action_taken,
                        ops_action_id=result.get("action_id"),
                    )
            else:
                action_taken += f" ({chosen.reason})"

            await db.update_incident(incident_id, IncidentStatus.remediating, root_cause, action_taken)
            recovered, note = await self._verify(anomaly, chosen)
            await self._close(
                incident_id,
                anomaly.anomaly_id,
                "resolved" if recovered else "failed",
                root_cause,
                f"{action_taken}; {note}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an incident must always reach a terminal state
            logger.error("healer.incident_failed", incident_id=str(incident_id), error=str(exc))
            await self._close(incident_id, anomaly.anomaly_id, "failed", root_cause, f"error: {exc}")

    async def _stale_reason(self, anomaly: AnomalyEvent) -> str | None:
        """Why this anomaly should not be acted on, or None to proceed.
        Telemetry being unreachable does not block the action: the Monitor's
        detection already justified it, this is only a staleness guard."""
        settings = self._settings
        thresholds = resolve_thresholds(anomaly.service, settings.default_thresholds, settings.service_thresholds)
        try:
            fresh = await check_still_failing(
                self._metrics,
                anomaly.service,
                anomaly.metric,
                thresholds,
                settings.precheck_window_seconds,
                settings.precheck_recent_samples,
            )
        except MetricsUnavailableError as exc:
            logger.warning("healer.precheck_unavailable", error=str(exc))
            return None
        if fresh == "recovered":
            return "the failure had already stopped by the time it was checked (stale anomaly)"
        if fresh == "no_traffic":
            return "no recent traffic to confirm the failure is ongoing; the Monitor will re-alert if it recurs"
        return None

    async def _allowed_actions(self) -> list[dict[str, Any]]:
        try:
            return await self._ops.actions()
        except OpsError:
            return describe_allowlist()

    # --- verification -----------------------------------------------------

    async def _verify(self, anomaly: AnomalyEvent, chosen: Plan) -> tuple[bool, str]:
        """ "Recovered" means the Monitor would not currently alert on this
        anomaly — either because the metric is genuinely back within
        thresholds, or because there has been no traffic to evaluate for
        two consecutive checks (~30s). The second case is deliberately
        treated as resolved for any anomaly, not only when the acted-on
        dependency's breaker is still open: a breaker can legitimately
        self-heal (a half-open probe against a *partial*-failure fault has
        a real chance of succeeding) between one verify check and the
        next, and by the time that happens there is nothing left to
        observe either way. Requiring the breaker to still be open to
        accept that silence — an earlier version of this check did —
        left an incident permanently stuck once the breaker closed on its
        own with no further traffic: found by a chaos test, not by
        inspection (see docs/decisions/ADR-0018). If the underlying
        problem is still real, the Monitor's own re-alert path (a fresh
        anomaly.detected once traffic resumes and actually fails again)
        is what catches it — this verifier's job is only to say whether
        there is current evidence of a problem, not to prove a negative."""

        settings = self._settings
        thresholds = resolve_thresholds(anomaly.service, settings.default_thresholds, settings.service_thresholds)
        deadline = time.monotonic() + settings.verify_timeout_seconds
        last, quiet_checks = "not checked", 0

        while time.monotonic() < deadline:
            await asyncio.sleep(settings.verify_interval_seconds)
            try:
                last = await check_recovery(
                    self._metrics, anomaly.service, anomaly.metric, thresholds, settings.verify_window_seconds
                )
            except MetricsUnavailableError as exc:
                last = f"telemetry unavailable ({exc})"
                continue
            if last == "recovered":
                return True, "verified: metric back within thresholds"
            quiet_checks = quiet_checks + 1 if last == "no_traffic" else 0
            if quiet_checks >= 2:
                return True, "resolved: no reproducing traffic — nothing currently observable to fail"
        return False, f"not verified within {settings.verify_timeout_seconds}s (last check: {last})"

    # --- closing out ------------------------------------------------------

    async def _close(
        self,
        incident_id: uuid.UUID,
        anomaly_id: uuid.UUID,
        outcome: str,
        root_cause: str | None,
        action_taken: str,
    ) -> None:
        status = IncidentStatus.resolved if outcome == "resolved" else IncidentStatus.failed
        resolved_at = await db.update_incident(incident_id, status, root_cause, action_taken)
        logger.warning(
            "healer.incident_closed", incident_id=str(incident_id), outcome=outcome, action_taken=action_taken
        )
        await self._publish(
            Channels.INCIDENT_RESOLVED,
            incident_id=str(incident_id),
            anomaly_id=str(anomaly_id),
            outcome=outcome,
            root_cause=root_cause,
            action_taken=action_taken,
            resolved_at=(resolved_at or datetime.now(UTC)).isoformat(),
        )

    async def _publish(self, channel: str, **fields: Any) -> None:
        try:
            await self._bus.publish(channel, {"event": channel, "timestamp": datetime.now(UTC).isoformat(), **fields})
        except Exception as exc:  # noqa: BLE001 - the durable record is the DB row; events are best-effort
            logger.error("healer.publish_failed", channel=channel, error=str(exc))
