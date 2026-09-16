"""Carries an optional reasoning-delta sink across a discovery/planning turn
without threading it through every node function's signature or LangGraph's
checkpointed state (a callable can't be serialized to a checkpoint, and
GraphState is a plain TypedDict every node already shares).

A contextvars.ContextVar is the right tool here, not a plain module-global:
each request runs in its own asyncio Task, and a Task copies the context it
was created in — so concurrent turns for different sessions never see each
other's sink, with no locking and no per-session registry to clean up.
LLMGateway reads this (see gateway.py) and wraps it with the node_name it
already knows before handing a plain per-call callback down to the provider;
callers that never set a sink (every existing test, every non-streaming
caller) see get_reasoning_sink() return None and nothing changes for them.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from contextlib import contextmanager

# (node_name, text_delta) -> None. Awaited once per delta as it streams in —
# keep whatever's on the other end fast (e.g. asyncio.Queue.put_nowait), since
# this runs inline in the token-consumption loop of a live provider call.
ReasoningSink = Callable[[str, str], Awaitable[None]]

_sink: contextvars.ContextVar[ReasoningSink | None] = contextvars.ContextVar("_reasoning_sink", default=None)


def get_reasoning_sink() -> ReasoningSink | None:
    return _sink.get()


@contextmanager
def reasoning_sink_scope(sink: ReasoningSink | None):
    """Makes `sink` the active reasoning-delta destination for every
    gateway.complete() call made anywhere within this block (and in any task
    spawned from within it) — see module docstring. Pass None for a no-op
    scope (equivalent to not entering it at all); used when a caller wants
    the same code path with streaming simply turned off.
    """

    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
