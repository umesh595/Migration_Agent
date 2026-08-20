from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_db
from app.llm.gateway import LLMGateway
from app.security.rate_limit import RateLimiter
from app.security.tokens import TokenError, decode_token

_bearer = HTTPBearer(auto_error=True)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    try:
        decoded = decode_token(credentials.credentials, "access")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    result = await db.execute(select(User).where(User.id == decoded.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    if decoded.token_version != user.token_version:
        # The user's token_version has moved on (password change, revoke-all) since
        # this access token was issued — it's structurally still valid but must no
        # longer be honored.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token has been revoked")
    if not user.is_active:
        # Checked on every request, not just at login: an admin disabling a user must
        # take effect immediately rather than waiting out that user's still-valid
        # access token (up to ACCESS_TOKEN_TTL_MINUTES later).
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account is disabled")
    return user


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required")
    return user


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_gateway(request: Request) -> LLMGateway:
    return request.app.state.gateway


async def enforce_rate_limit(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    allowed = await limiter.check(
        key=f"user:{user.id}:requests",
        limit=settings.rate_limit_requests_per_minute,
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")


async def enforce_message_rate_limit(
    user: Annotated[User, Depends(current_user)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Tighter limit on the expensive path — each message triggers LLM calls."""

    allowed = await limiter.check(
        key=f"user:{user.id}:messages",
        limit=settings.rate_limit_messages_per_minute,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="message rate limit exceeded — planning turns are rate limited per minute",
        )


CurrentUser = Annotated[User, Depends(current_user)]
Db = Annotated[AsyncSession, Depends(get_db)]


def parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid {field}") from exc
