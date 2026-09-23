import hmac

from fastapi import APIRouter, Request

from agents.ops_controller import audit
from agents.ops_controller.allowlist import ActionRejected, describe_allowlist, validate_action
from agents.ops_controller.config import get_settings
from agents.ops_controller.db import SessionLocal
from shared.errors import (
    DependencyUnavailableError,
    RateLimitedError,
    UnauthorizedError,
    ValidationAppError,
)
from shared.logging import get_logger
from shared.schemas import Envelope

logger = get_logger(__name__)

router = APIRouter(prefix="/ops", tags=["ops"])


def _authenticate(request: Request) -> str | None:
    """Only the Healer holds this token. Constant-time compare."""
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        presented = header.removeprefix("Bearer ").strip().encode()
        if hmac.compare_digest(presented, get_settings().healer_token.encode()):
            return "healer"
    return None


def _require_caller(request: Request) -> str:
    caller = _authenticate(request)
    if caller is None:
        raise UnauthorizedError("invalid or missing Ops Controller token")
    return caller


@router.get("/actions", response_model=Envelope[list[dict]])
async def list_actions(request: Request) -> Envelope[list[dict]]:
    _require_caller(request)
    return Envelope(data=describe_allowlist())


@router.get("/circuits", response_model=Envelope[dict[str, str]])
async def circuit_states(request: Request) -> Envelope[dict[str, str]]:
    """Read-only view of Payment's breaker states, so the Healer can see
    whether an action is even needed."""
    _require_caller(request)
    return Envelope(data=await request.app.state.executor.circuit_states("payment"))


# One explicit route per action rather than a generic /actions/{name}: the
# set of reachable operations is then visible in the route table itself,
# and a new allowlist entry can't become reachable by accident.
@router.post("/actions/open-circuit", response_model=Envelope[dict])
async def open_circuit(request: Request) -> Envelope[dict]:
    return await _run_action(request, "open-circuit")


@router.post("/actions/reset-circuit", response_model=Envelope[dict])
async def reset_circuit(request: Request) -> Envelope[dict]:
    return await _run_action(request, "reset-circuit")


async def _run_action(request: Request, action: str) -> Envelope[dict]:
    try:
        raw = await request.json()
    except ValueError:
        raw = {"_invalid_json": True}
    caller = _authenticate(request)

    async with SessionLocal() as db:
        row = await audit.record_received(db, action, raw, caller or "unauthenticated")

        if caller is None:
            await audit.finish(db, row, "rejected", "authentication failed")
            logger.warning("ops.rejected", action=action, reason="unauthenticated", action_id=str(row.id))
            raise UnauthorizedError("invalid or missing Ops Controller token")

        try:
            params = validate_action(action, raw)
        except ActionRejected as exc:
            await audit.finish(db, row, "rejected", str(exc))
            logger.warning("ops.rejected", action=action, reason=str(exc), action_id=str(row.id))
            raise ValidationAppError(str(exc)) from exc

        settings = get_settings()
        cooldown_key = f"ops:cooldown:{action}:{params.service}:{params.dependency}"
        redis_client = request.app.state.redis
        if not await redis_client.set(cooldown_key, "1", nx=True, ex=settings.action_cooldown_seconds):
            await audit.finish(db, row, "rejected", "cooldown: same action on same target too recent")
            logger.warning("ops.rejected", action=action, reason="cooldown", action_id=str(row.id))
            raise RateLimitedError("this action on this target ran too recently, try again later")

        try:
            state = await request.app.state.executor.set_circuit(action, params.service, params.dependency)
        except DependencyUnavailableError as exc:
            await redis_client.delete(cooldown_key)  # nothing happened, so don't burn the cooldown
            await audit.finish(db, row, "failed", str(exc))
            logger.error("ops.failed", action=action, error=str(exc), action_id=str(row.id))
            raise

        detail = f"{params.service}:{params.dependency} breaker now {state}"
        await audit.finish(db, row, "executed", detail)
        logger.warning("ops.executed", action=action, detail=detail, caller=caller, action_id=str(row.id))
        return Envelope(data={"status": "executed", "action_id": str(row.id), "detail": detail})
