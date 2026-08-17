"""Admin user provisioning (FR-A5). There is no self-service registration anywhere
in this codebase — every account is created here (by an existing admin) or once, at
startup, by the bootstrap step in app/main.py using BOOTSTRAP_ADMIN_EMAIL/PASSWORD.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import Db, require_admin
from app.db.models import User
from app.services import user_service
from app.services.user_service import UserServiceError

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, description="Minimum 12 characters.")
    is_admin: bool = False


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool
    is_active: bool

    @classmethod
    def from_model(cls, user: User) -> UserResponse:
        return cls(id=user.id, email=user.email, is_admin=user.is_admin, is_active=user.is_active)


class SetActiveRequest(BaseModel):
    active: bool


class ResetPasswordResponse(BaseModel):
    id: uuid.UUID
    email: str
    temporary_password: str = Field(description="Shown once. Relay to the user out of band; not stored or logged.")


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: CreateUserRequest, db: Db) -> UserResponse:
    try:
        user = await user_service.create_user(
            db, email=payload.email, password=payload.password, is_admin=payload.is_admin
        )
    except UserServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserResponse.from_model(user)


@router.get("/users", response_model=list[UserResponse])
async def list_users(db: Db) -> list[UserResponse]:
    users = await user_service.list_users(db)
    return [UserResponse.from_model(u) for u in users]


@router.patch("/users/{user_id}/active", response_model=UserResponse)
async def set_active(user_id: uuid.UUID, payload: SetActiveRequest, db: Db) -> UserResponse:
    """Disables or re-enables a user (FR-A5 'disable'). Deactivating does not delete
    anything — that user's sessions, models, and plans remain intact and auditable."""

    try:
        user = await user_service.set_active(db, user_id, active=payload.active)
    except UserServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return UserResponse.from_model(user)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(user_id: uuid.UUID, db: Db) -> ResetPasswordResponse:
    """Issues a new random password (FR-A5 'reset'). Returned in plaintext exactly
    once in this response — never persisted or logged anywhere as plaintext."""

    try:
        user, temp_password = await user_service.reset_password(db, user_id)
    except UserServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ResetPasswordResponse(id=user.id, email=user.email, temporary_password=temp_password)
