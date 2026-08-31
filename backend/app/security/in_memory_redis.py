from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any


class InMemoryRedis:
    """Tiny Redis substitute for local single-process development.

    It intentionally supports only the commands this app uses for rate limiting
    and session turn locks. Production must use real Redis.
    """

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, float | None]] = {}

    def register_script(self, script: str) -> Callable[..., Awaitable[int]]:
        if "INCR" in script and "EXPIRE" in script:
            return self._rate_limit_script
        return self._release_if_owner_script

    async def set(self, key: str, value: str, *, nx: bool = False, px: int | None = None) -> bool:
        self._purge_if_expired(key)
        if nx and key in self._values:
            return False
        expires_at = time.monotonic() + (px / 1000) if px is not None else None
        self._values[key] = (value, expires_at)
        return True

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self._values.clear()

    async def _rate_limit_script(self, *, keys: list[str], args: list[Any]) -> int:
        key = keys[0]
        window_seconds = int(args[0])
        self._purge_if_expired(key)
        current = int(self._values.get(key, ("0", None))[0]) + 1
        expires_at = self._values.get(key, ("", None))[1]
        if current == 1 or expires_at is None:
            expires_at = time.monotonic() + window_seconds
        self._values[key] = (str(current), expires_at)
        return current

    async def _release_if_owner_script(self, *, keys: list[str], args: list[Any]) -> int:
        key = keys[0]
        token = str(args[0])
        self._purge_if_expired(key)
        if self._values.get(key, (None, None))[0] == token:
            del self._values[key]
            return 1
        return 0

    def _purge_if_expired(self, key: str) -> None:
        stored = self._values.get(key)
        if stored is None:
            return
        _, expires_at = stored
        if expires_at is not None and expires_at <= time.monotonic():
            del self._values[key]
