from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

from app.config import get_settings

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    pass


def create_token(user_id: uuid.UUID, token_type: TokenType, token_version: int) -> str:
    """`token_version` is embedded so a bump on the user row (password change,
    explicit revoke) invalidates every token issued before the bump — decode_token
    checks it against the live DB value, not just the signature/expiry."""

    settings = get_settings()
    now = datetime.now(UTC)
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)


class DecodedToken:
    def __init__(self, user_id: uuid.UUID, token_version: int) -> None:
        self.user_id = user_id
        self.token_version = token_version


def decode_token(token: str, expected_type: TokenType) -> DecodedToken:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"expected a {expected_type} token")

    try:
        user_id = uuid.UUID(payload["sub"])
        token_version = int(payload["ver"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("malformed token subject") from exc

    return DecodedToken(user_id, token_version)
