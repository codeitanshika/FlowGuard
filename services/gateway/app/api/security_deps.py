from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.dependencies import get_rate_limiter
from app.core.security import TokenError, decode_access_token
from app.models.schemas import AuthenticatedClient
from shared.errors import RateLimitedError, UnauthorizedError


async def get_current_client(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedClient:
    """Authentication: verifies the bearer token and establishes *who* is
    calling. Does not decide what they're allowed to do — that's
    authorization.check_scope, applied per-route after this runs."""

    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthorizedError("missing or malformed Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token, settings.jwt_secret)
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc

    return AuthenticatedClient(client_id=payload["sub"], scopes=payload.get("scopes", []))


async def enforce_rate_limit(
    settings: Settings = Depends(get_settings),
    client: AuthenticatedClient = Depends(get_current_client),
) -> AuthenticatedClient:
    limiter = get_rate_limiter()
    if not await limiter.check(f"client:{client.client_id}", settings.rate_limit_per_minute):
        raise RateLimitedError(f"rate limit of {settings.rate_limit_per_minute}/min exceeded")
    return client
