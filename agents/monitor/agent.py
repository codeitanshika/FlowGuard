import asyncio
from datetime import UTC, datetime

from agents.monitor.alert_state import AlertDeduper
from agents.monitor.config import Settings
from agents.monitor.db import SessionLocal, save_anomaly
from agents.monitor.detector import Breach, compute_snapshot, evaluate
from agents.monitor.metrics_client import JaegerMetricsClient, MetricsUnavailableError
from agents.monitor.thresholds import resolve_thresholds
from shared.events import Channels, RedisEventBus
from shared.logging import get_logger

logger = get_logger(__name__)


class MonitorAgent:
    """Detection only: reads telemetry, writes its own `anomalies` table,
    publishes `anomaly.detected`. It holds no credentials for, and makes no
    calls to, any business service — see docs/architecture/03-service-boundaries.md."""

    def __init__(
        self,
        settings: Settings,
        metrics: JaegerMetricsClient,
        deduper: AlertDeduper,
        bus: RedisEventBus,
    ) -> None:
        self._settings = settings
        self._metrics = metrics
        self._deduper = deduper
        self._bus = bus

    async def run_forever(self) -> None:
        logger.info(
            "monitor.started",
            services=self._settings.monitored_services,
            poll_interval_seconds=self._settings.poll_interval_seconds,
            window_seconds=self._settings.window_seconds,
        )
        while True:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not end monitoring
                logger.error("monitor.cycle_failed", error=str(exc))
            await asyncio.sleep(self._settings.poll_interval_seconds)

    async def run_cycle(self) -> None:
        for service in self._settings.monitored_services:
            try:
                await self._check_service(service)
            except MetricsUnavailableError as exc:
                logger.warning("monitor.telemetry_unavailable", service=service, error=str(exc))
            except Exception as exc:  # noqa: BLE001 - isolate services from each other
                logger.error("monitor.service_check_failed", service=service, error=str(exc))

    async def _check_service(self, service: str) -> None:
        samples, truncated = await self._metrics.fetch_samples(service, self._settings.window_seconds)
        snapshot = compute_snapshot(service, samples, self._settings.window_seconds, truncated)
        logger.info(
            "monitor.snapshot",
            service=service,
            requests=snapshot.request_count,
            error_rate=round(snapshot.error_rate, 4),
            p95_ms=round(snapshot.p95_ms, 1),
            p99_ms=round(snapshot.p99_ms, 1),
            throughput_rps=round(snapshot.throughput_rps, 3),
            truncated=truncated,
        )
        if truncated:
            logger.warning(
                "monitor.telemetry_truncated", service=service, max_traces=self._settings.max_traces
            )

        thresholds = resolve_thresholds(
            service, self._settings.default_thresholds, self._settings.service_thresholds
        )
        for breach in evaluate(snapshot, thresholds):
            await self._raise_anomaly(breach)

    async def _raise_anomaly(self, breach: Breach) -> None:
        if not await self._deduper.try_claim(breach.service, breach.metric, breach.severity):
            logger.info(
                "monitor.anomaly_suppressed", service=breach.service, metric=breach.metric
            )
            return

        try:
            # Persist before publishing so a consumer that receives the
            # event can always find the row it refers to.
            async with SessionLocal() as db:
                anomaly = await save_anomaly(
                    db,
                    breach.service,
                    breach.metric,
                    breach.observed_value,
                    breach.threshold,
                    breach.severity,
                    breach.trace_id,
                )
            await self._bus.publish(
                Channels.ANOMALY_DETECTED,
                {
                    "event": Channels.ANOMALY_DETECTED,
                    "anomaly_id": str(anomaly.id),
                    "service": breach.service,
                    "metric": breach.metric,
                    "observed_value": breach.observed_value,
                    "threshold": breach.threshold,
                    "severity": breach.severity,
                    "trace_id": breach.trace_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        except Exception:
            await self._deduper.release(breach.service, breach.metric)
            raise

        logger.warning(
            "monitor.anomaly_detected",
            anomaly_id=str(anomaly.id),
            service=breach.service,
            metric=breach.metric,
            observed_value=breach.observed_value,
            threshold=breach.threshold,
            severity=breach.severity,
        )
