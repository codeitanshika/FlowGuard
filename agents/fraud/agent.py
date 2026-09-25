from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from agents.fraud import db
from agents.fraud.config import Settings
from agents.fraud.freeze_client import FreezeClient, FreezeError
from agents.fraud.geo import is_geo_anomaly
from agents.fraud.narrative import Narrator, PROMPT_VERSION
from agents.fraud.risk import decide
from agents.fraud.schemas import PaymentCreatedEvent
from agents.fraud.velocity import VelocityTracker
from shared.events import Channels, RedisEventBus, consume_forever
from shared.logging import get_logger

logger = get_logger(__name__)


class FraudAgent:
    """payment.created -> velocity + simulated geo -> deterministic risk
    level -> high freezes (cooldown-bounded), borderline gets an LLM/rules
    narrative logged for human review, low does nothing. Never touches
    users.status itself — only User Service's freeze endpoint does that
    (docs/architecture/03-service-boundaries.md)."""

    def __init__(
        self,
        settings: Settings,
        bus: RedisEventBus,
        velocity: VelocityTracker,
        freeze_client: FreezeClient,
        narrator: Narrator,
        cooldown_redis: redis.Redis,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._velocity = velocity
        self._freeze_client = freeze_client
        self._narrator = narrator
        self._redis = cooldown_redis

    async def run_forever(self) -> None:
        await consume_forever(
            self._bus, Channels.PAYMENT_CREATED, on_message=self._on_message, service_name="fraud_agent"
        )

    async def _on_message(self, message: dict) -> None:
        await self.process_raw_message(message["data"])

    async def process_raw_message(self, data: str) -> None:
        try:
            event = PaymentCreatedEvent.model_validate_json(data)
        except ValueError as exc:
            # Poison event (failure scenario 10): log and drop, never crash.
            logger.error("event.invalid", error=str(exc)[:300])
            return
        try:
            await self._handle(event)
        except Exception as exc:  # noqa: BLE001 - one bad event must not kill the consumer loop
            logger.error("fraud_agent.event_failed", transaction_id=str(event.transaction_id), error=str(exc))

    async def _handle(self, event: PaymentCreatedEvent) -> None:
        velocity_count = await self._velocity.record_and_count(event.user_id, event.transaction_id)
        geo_anomaly, home, observed = is_geo_anomaly(
            event.user_id, event.transaction_id, self._settings.thresholds.geo_mismatch_denominator
        )
        risk = decide(velocity_count, geo_anomaly, self._settings.thresholds)

        logger.info(
            "fraud_agent.assessed",
            transaction_id=str(event.transaction_id),
            user_id=str(event.user_id),
            level=risk.level,
            velocity_count=velocity_count,
            geo_anomaly=geo_anomaly,
        )

        if risk.level == "high":
            await self._handle_high(event, risk, home, observed)
        elif risk.level == "borderline":
            await self._handle_borderline(event, risk, home, observed)
        # "low" is intentionally not persisted — see agents/fraud/db.py's
        # docstring reasoning and docs/CODE_STANDARDS.md: only noteworthy
        # decisions become an audit row, the same choice the Monitor makes.

    async def _handle_high(self, event: PaymentCreatedEvent, risk, home: str, observed: str) -> None:
        reason = "; ".join(risk.reasons)
        input_summary = self._input_summary(event, risk, home, observed)

        cooldown_key = f"fraud:freeze_cooldown:{event.user_id}"
        if not await self._redis.set(cooldown_key, "1", nx=True, ex=self._settings.freeze_cooldown_seconds):
            logger.info("fraud_agent.freeze_skipped_cooldown", user_id=str(event.user_id))
            await db.record_decision(
                input_summary, {"level": "high", "reason": reason, "outcome": "skipped_cooldown"}, executed=False
            )
            return

        try:
            await self._freeze_client.freeze(event.user_id, reason)
        except FreezeError as exc:
            await self._redis.delete(cooldown_key)  # nothing happened, don't burn the cooldown
            logger.error("fraud_agent.freeze_failed", user_id=str(event.user_id), error=str(exc))
            await db.record_decision(
                input_summary, {"level": "high", "reason": reason, "outcome": f"freeze_failed: {exc}"}, executed=False
            )
            return

        await db.record_decision(
            input_summary, {"level": "high", "reason": reason, "outcome": "frozen"}, executed=True
        )
        logger.warning("fraud_agent.user_frozen", user_id=str(event.user_id), reason=reason)
        await self._publish_frozen(event, reason)

    async def _handle_borderline(self, event: PaymentCreatedEvent, risk, home: str, observed: str) -> None:
        narrative = await self._narrator.narrate(risk)
        input_summary = self._input_summary(event, risk, home, observed)
        input_summary["source"] = narrative.source
        input_summary["fallback_reason"] = narrative.fallback_reason

        await db.record_decision(
            input_summary,
            {
                "level": "borderline",
                "rationale": narrative.rationale,
                "confidence": narrative.confidence,
                "outcome": "logged_for_review",
            },
            executed=False,
            llm_model=narrative.llm_model,
            llm_prompt_version=PROMPT_VERSION if narrative.source == "llm" else None,
            llm_latency_ms=narrative.latency_ms,
            llm_tokens_in=narrative.tokens_in,
            llm_tokens_out=narrative.tokens_out,
        )
        logger.info(
            "fraud_agent.flagged_for_review",
            user_id=str(event.user_id),
            confidence=narrative.confidence,
            source=narrative.source,
        )

    async def _publish_frozen(self, event: PaymentCreatedEvent, reason: str) -> None:
        try:
            await self._bus.publish(
                Channels.FRAUD_USER_FROZEN,
                {
                    "event": Channels.FRAUD_USER_FROZEN,
                    "user_id": str(event.user_id),
                    "reason": reason,
                    "risk_assessment_id": None,
                    "trace_id": event.trace_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - the durable record is the DB row + User Service's freeze_events
            logger.error("fraud_agent.publish_failed", error=str(exc))

    def _input_summary(self, event: PaymentCreatedEvent, risk, home: str, observed: str) -> dict[str, Any]:
        return {
            "transaction_id": str(event.transaction_id),
            "user_id": str(event.user_id),
            "amount": str(event.amount),
            "velocity_count": risk.velocity_count,
            "geo_anomaly": risk.geo_anomaly,
            "home_country": home,
            "observed_country": observed,
        }
