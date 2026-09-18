import time

import bcrypt
import jwt

ALGORITHM = "HS256"
ISSUER = "flowguard-gateway"


class TokenError(Exception):
    pass


def create_access_token(
    subject: str, scopes: list[str], secret: str, expires_in: int
) -> tuple[str, int]:
    now = int(time.time())
    payload = {
        "sub": subject,
        "scopes": scopes,
        "iat": now,
        "exp": now + expires_in,
        "iss": ISSUER,
    }
    token = jwt.encode(payload, secret, algorithm=ALGORITHM)
    return token, expires_in


def decode_access_token(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=ISSUER)
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc


def verify_secret(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())
