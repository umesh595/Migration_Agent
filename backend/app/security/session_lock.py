"""Per-session turn serialization, backed by Redis (mandatory dependency — see
rate_limit.py for why an in-process lock can't work: the API is stateless and
scales horizontally, so two concurrent turns for the same session can land on
different replicas).

Why this exists: message_id idempotency (session_service.claim_message) prevents
a REPLAYED/double-submitted request from re-running a turn, but it does nothing
for two genuinely DIFFERENT messages sent concurrently for the same session (two
browser tabs, a scripted client, a retried request racing its own follow-up).
Both would read the same latest ModelVersion, both advance the LangGraph
checkpoint for the same thread_id, and both attempt to insert the same next
version — the loser hits the DB's uq_model_version_per_session constraint and
raises an unhandled IntegrityError. A session's turns must be strictly
serialized end-to-end (read model -> run graph -> persist), not just
deduplicated by message_id.

SET NX PX is atomic and safe to use with a connection-pooled Redis client (unlike
a Postgres advisory lock over a pooled SQLAlchemy session, where the underlying
connection can change between statements and leak the lock). The TTL is a safety
net against a crashed holder, not the primary release mechanism — the lock is
always explicitly released in a `finally` block.
"""

from __future__ import annotations

import logging
import uuid

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_RELEASE_IF_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class SessionBusyError(Exception):
    """Raised when another turn is already in flight for this session."""


class SessionTurnLock:
    """Explicit acquire/release rather than a context manager: the critical
    section here is the lifetime of an SSE generator (app/api/routers/sessions.py
    post_message's event_stream), which outlives the request-handler stack frame
    that creates it — a context manager entered in that frame would release the
    lock before the generator body ever runs. Acquire happens before the
    EventSourceResponse is returned (so a busy session 409s immediately, not as
    a silently-empty stream); release happens in the generator's own
    try/finally once persistence actually completes or the turn fails."""

    def __init__(self, redis: Redis, *, ttl_seconds: int = 120) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._release_script = redis.register_script(_RELEASE_IF_OWNER)

    def key_for(self, session_id: str) -> str:
        return f"session-turn-lock:{session_id}"

    async def acquire(self, session_id: str) -> str:
        """Returns an opaque ownership token on success. Raises SessionBusyError
        immediately (never blocks/queues) if another turn already holds the
        lock — an in-flight turn can run for the full LLM planning pass, so
        making a second request wait silently would just turn into a
        client-side timeout with no useful signal; failing fast lets the
        caller retry deliberately."""

        token = str(uuid.uuid4())
        acquired = await self._redis.set(self.key_for(session_id), token, nx=True, px=self._ttl_seconds * 1000)
        if not acquired:
            raise SessionBusyError(f"a turn is already in progress for session {session_id}")
        return token

    async def release(self, session_id: str, token: str) -> None:
        try:
            await self._release_script(keys=[self.key_for(session_id)], args=[token])
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            # The TTL still bounds the lock's lifetime even if this fails, so a
            # failed release degrades to "wait out the TTL", not a permanent
            # deadlock — logged so a pattern of failures is visible.
            logger.warning("failed to release session turn lock for %s: %s", session_id, exc)
