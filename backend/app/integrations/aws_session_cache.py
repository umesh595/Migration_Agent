"""Session-scoped AWS connection cache — the backbone of cloud-discovery-first
(discovery_agent_dynamic_spec.md §2): before falling back to asking the user a
purely technical/structural question, check whether it can be answered from a
live, read-only cloud scan instead.

In-process, keyed by session_id, with a TTL on the fetched inventory (not the
credentials, which live for the process's lifetime once connected — the
"session-scoped, never persisted" decision from the AWS import endpoint
applies here too: nothing here is written to the database, and a process
restart clears everything). A single-process dev/small-deployment cache like
this is consistent with app/api/routers/ag_ui.py's `_agent_cache` precedent in
this codebase — the honest limitation is the same one noted there: a
multi-replica production deployment would need this in Redis (with the same
per-session isolation and TTL), not a bare process dict, since replicas don't
share memory. Flagged here rather than silently assumed away.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.integrations.aws_provider import AWSCredentials, AWSFetchResult

_INVENTORY_TTL_SECONDS = 600  # re-scan at most every 10 minutes per session


@dataclass
class _CachedConnection:
    credentials: AWSCredentials
    inventory: AWSFetchResult | None = None
    inventory_fetched_at: float = 0.0


_connections: dict[str, _CachedConnection] = {}


def connect(session_id: str, credentials: AWSCredentials) -> None:
    _connections[session_id] = _CachedConnection(credentials=credentials)


def disconnect(session_id: str) -> bool:
    return _connections.pop(session_id, None) is not None


def is_connected(session_id: str) -> bool:
    return session_id in _connections


def get_credentials(session_id: str) -> AWSCredentials | None:
    entry = _connections.get(session_id)
    return entry.credentials if entry else None


def get_cached_inventory(session_id: str) -> AWSFetchResult | None:
    """None if never fetched or the cached copy is past its TTL — the caller
    (cloud_discovery.py) is responsible for re-fetching and calling
    store_inventory() in that case."""

    entry = _connections.get(session_id)
    if entry is None or entry.inventory is None:
        return None
    if time.monotonic() - entry.inventory_fetched_at > _INVENTORY_TTL_SECONDS:
        return None
    return entry.inventory


def store_inventory(session_id: str, inventory: AWSFetchResult) -> None:
    entry = _connections.get(session_id)
    if entry is None:
        return
    entry.inventory = inventory
    entry.inventory_fetched_at = time.monotonic()
