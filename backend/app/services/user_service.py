"""Admin-provisioned user lifecycle (FR-A5). There is deliberately no self-service
signup path anywhere in this module or its callers — every account is created either
by the one-time startup bootstrap (below) or by an existing admin via the /admin
endpoints (app/api/routers/admin.py)."""

from __future__ import annotations

import logging
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.security.passwords import hash_password

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """Raised on invalid admin operations (duplicate email, unknown user, etc.).
    Routers turn this into a 4xx — never a 500 for an expected business condition."""


def generate_temp_password() -> str:
    """URL-safe, high-entropy — handed to an admin once, never logged or stored."""

    return secrets.token_urlsafe(18)


async def create_user(
    db: AsyncSession, *, email: str, password: str, is_admin: bool = False
) -> User:
    normalized = email.lower()
    existing = await db.execute(select(User).where(User.email == normalized))
    if existing.scalar_one_or_none() is not None:
        raise UserServiceError(f"a user with email '{normalized}' already exists")

    user = User(email=normalized, hashed_password=hash_password(password), is_admin=is_admin)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars().all())


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise UserServiceError("user not found")
    return user


async def set_active(db: AsyncSession, user_id: uuid.UUID, *, active: bool) -> User:
    user = await get_user(db, user_id)
    user.is_active = active
    await db.commit()
    await db.refresh(user)
    return user


async def reset_password(db: AsyncSession, user_id: uuid.UUID) -> tuple[User, str]:
    """Issues a fresh random password and returns it in plaintext exactly once —
    the caller (an admin) is responsible for relaying it to the user out of band.
    Nothing else in the system ever sees or logs the plaintext value."""

    user = await get_user(db, user_id)
    temp_password = generate_temp_password()
    user.hashed_password = hash_password(temp_password)
    await db.commit()
    await db.refresh(user)
    return user, temp_password


async def bootstrap_admin_if_configured(
    db: AsyncSession, *, email: str | None, password: str | None
) -> None:
    """Creates the first admin from env-provided credentials, but only if no admin
    exists yet anywhere in the system. Idempotent across restarts: once an admin
    exists, this is a no-op even if the env vars are still set (so leaving them in
    a long-running deployment's config is harmless, not a standing re-provision risk).
    """

    if not email or not password:
        return

    existing_admin = await db.execute(select(func.count()).select_from(User).where(User.is_admin.is_(True)))
    if existing_admin.scalar_one() > 0:
        return

    normalized = email.lower()
    existing_user = await db.execute(select(User).where(User.email == normalized))
    user = existing_user.scalar_one_or_none()
    if user is not None:
        # Email already registered as a non-admin — promote rather than duplicate.
        user.is_admin = True
        await db.commit()
        logger.info("promoted existing user %s to admin via bootstrap config", normalized)
        return

    await create_user(db, email=normalized, password=password, is_admin=True)
    logger.info("bootstrap admin account created for %s", normalized)
