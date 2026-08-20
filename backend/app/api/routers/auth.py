"""Login/refresh only. There is deliberately no signup endpoint here — FR-A5
requires admin-provisioned users with no self-service registration in v1. Accounts
are created by an existing admin (app/api/routers/admin.py) or by the one-time
startup bootstrap (app/main.py, app/services/user_service.py)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, Db
from app.db.models import User
from app.db.session import get_db
from app.security.passwords import hash_password, verify_password
from app.security.tokens import TokenError, create_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, description="Minimum 12 characters.")


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenPair:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()

    # Same error for unknown email, wrong password, and disabled account — don't
    # leak which emails exist or which accounts have been deactivated.
    if user is None or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    return TokenPair(
        access_token=create_token(user.id, "access", user.token_version),
        refresh_token=create_token(user.id, "refresh", user.token_version),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenPair:
    """Refresh tokens are NOT single-use here (no stored jti/reuse-detection store),
    but every issued token — access or refresh — carries the token_version that was
    live at issuance. change_password (below) bumps that version, which immediately
    invalidates every refresh token issued before the change, closing the window a
    previously stolen refresh token would otherwise have for its full TTL."""

    try:
        decoded = decode_token(payload.refresh_token, "refresh")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    result = await db.execute(select(User).where(User.id == decoded.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or disabled")
    if decoded.token_version != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token has been revoked")

    return TokenPair(
        access_token=create_token(user.id, "access", user.token_version),
        refresh_token=create_token(user.id, "refresh", user.token_version),
    )


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    """Lets a client (the web app) know who's logged in and whether to show admin
    UI, without embedding is_admin in the JWT — that would go stale the moment an
    admin's role changed until their token naturally expired."""

    return MeResponse(id=user.id, email=user.email, is_admin=user.is_admin)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(payload: ChangePasswordRequest, user: CurrentUser, db: Db) -> None:
    """Self-service password change for an already-authenticated user — distinct
    from admin-provisioned account creation (FR-A5) and admin password reset. The
    bootstrap admin has no other way to rotate the seed password out of
    BOOTSTRAP_ADMIN_PASSWORD without this."""

    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect")

    user.hashed_password = hash_password(payload.new_password)
    # Invalidates every access/refresh token issued before this change — including
    # any refresh token an attacker may have already captured.
    user.token_version += 1
    await db.commit()


@router.post("/logout-everywhere", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(user: CurrentUser, db: Db) -> None:
    """Revokes every outstanding access and refresh token for this user immediately,
    without requiring a password change — the response to 'I think my session/refresh
    token leaked' that doesn't force picking a new password."""

    user.token_version += 1
    await db.commit()
