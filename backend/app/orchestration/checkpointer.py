"""LangGraph Postgres checkpointer lifecycle. One saver is shared process-wide;
`setup()` creates langgraph's own tables (separate from Alembic — see db/models.py)."""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def _psycopg_dsn() -> str:
    """SQLAlchemy uses postgresql+psycopg://; psycopg wants a plain DSN."""

    return str(get_settings().database_url).replace("postgresql+psycopg://", "postgresql://")


async def init_checkpointer() -> AsyncPostgresSaver:
    global _pool, _saver
    if _saver is not None:
        return _saver

    _pool = AsyncConnectionPool(conninfo=_psycopg_dsn(), max_size=10, open=False, kwargs={"autocommit": True})
    await _pool.open()
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()
    logger.info("langgraph postgres checkpointer ready")
    return _saver


def get_checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise RuntimeError("checkpointer not initialized — call init_checkpointer() during app startup")
    return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None
