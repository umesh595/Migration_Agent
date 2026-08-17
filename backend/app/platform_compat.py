"""Windows event-loop compatibility.

psycopg's async driver cannot run on Windows' default ProactorEventLoop and raises
InterfaceError on the first connection. Anything that opens an async Postgres
connection on Windows must run on a SelectorEventLoop instead.

`loop_factory` is used rather than the older `set_event_loop_policy`, which is
deprecated from Python 3.14 and slated for removal. Linux/macOS are unaffected —
their default loop already works — so this is a no-op there.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any


def loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """Returns a loop factory suitable for asyncio.run(), or None to use the default."""

    if sys.platform == "win32":
        return asyncio.SelectorEventLoop
    return None


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    """asyncio.run() on a psycopg-compatible loop for the current platform."""

    factory = loop_factory()
    if factory is None:
        return asyncio.run(coro)
    return asyncio.run(coro, loop_factory=factory)
