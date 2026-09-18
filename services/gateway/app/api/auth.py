from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.core.dependencies import get_rate_limiter
from app.core.security import create_access_token, verify_secret
from app.models.schemas import LoginRequest, TokenResponse
from shared.errors import RateLimitedError, UnauthorizedError
from shared.schemas import Envelope

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=Envelope[TokenResponse])
async def login(
    request: Request,
    payload: LoginRequest,
    settings: Settings = Depends(get_settings),
) -> Envelope[TokenResponse]:
    # Rate-limited by caller IP, not client_id — at this point in the flow
    # the caller hasn't proven who they are yet, so IP is the only thing
    # available to key on. This caps brute-force credential guessing.
    client_host = request.client.host if request.client else "unknown"
    limiter = get_rate_limiter()
    if not await limiter.check(f"login:{client_host}", settings.login_rate_limit_per_minute):
        raise RateLimitedError("too many login attempts, try again shortly")

    credential = settings.clients.get(payload.client_id)
    if credential is None or not verify_secret(payload.client_secret, credential.secret_hash):
        raise UnauthorizedError("invalid client credentials")

    token, expires_in = create_access_token(
        subject=payload.client_id,
        scopes=credential.scopes,
        secret=settings.jwt_secret,
        expires_in=settings.jwt_expires_in,
    )
    return Envelope(data=TokenResponse(access_token=token, expires_in=expires_in))
