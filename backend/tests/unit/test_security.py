"""Security-path tests: token validation, password verification, and rate-limiter
failure behavior. These paths only run when something is wrong or hostile, so they
never get exercised by happy-path tests — which is exactly why they need explicit
coverage."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import RateLimiter
from app.security.tokens import TokenError, create_token, decode_token


class TestPasswords:
    def test_correct_password_verifies(self):
        hashed = hash_password("a-sufficiently-long-password")
        assert verify_password("a-sufficiently-long-password", hashed)

    def test_wrong_password_rejected(self):
        hashed = hash_password("a-sufficiently-long-password")
        assert not verify_password("wrong-password", hashed)

    def test_same_password_produces_different_hashes(self):
        """Distinct salts — two users with the same password must not share a hash."""

        assert hash_password("identical-password") != hash_password("identical-password")

    def test_malformed_hash_returns_false_instead_of_raising(self):
        """A corrupted DB value must fail closed, not 500 the login endpoint."""

        assert not verify_password("any-password", "not-a-valid-bcrypt-hash")


class TestTokens:
    def test_roundtrip_access_token(self):
        user_id = uuid.uuid4()
        decoded = decode_token(create_token(user_id, "access", token_version=0), "access")
        assert decoded.user_id == user_id
        assert decoded.token_version == 0

    def test_token_version_is_carried_through(self):
        user_id = uuid.uuid4()
        decoded = decode_token(create_token(user_id, "access", token_version=3), "access")
        assert decoded.token_version == 3

    def test_refresh_token_rejected_where_access_expected(self):
        """Token-type confusion: a long-lived refresh token must not authenticate
        API requests."""

        token = create_token(uuid.uuid4(), "refresh", token_version=0)
        with pytest.raises(TokenError, match="expected a access token"):
            decode_token(token, "access")

    def test_expired_token_rejected(self):
        settings = get_settings()
        payload = {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
            "jti": str(uuid.uuid4()),
        }
        expired = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)

        with pytest.raises(TokenError, match="expired"):
            decode_token(expired, "access")

    def test_token_signed_with_wrong_secret_rejected(self):
        payload = {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        forged = jwt.encode(payload, "an-attacker-controlled-secret", algorithm="HS256")

        with pytest.raises(TokenError, match="invalid token"):
            decode_token(forged, "access")

    def test_garbage_token_rejected(self):
        with pytest.raises(TokenError):
            decode_token("not.a.jwt", "access")

    def test_token_with_non_uuid_subject_rejected(self):
        settings = get_settings()
        payload = {
            "sub": "not-a-uuid",
            "type": "access",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)

        with pytest.raises(TokenError, match="malformed token subject"):
            decode_token(token, "access")


class _ExplodingRedis:
    """Stands in for Redis being unreachable."""

    def register_script(self, _script):
        async def _raise(*args, **kwargs):
            raise ConnectionError("redis is down")

        return _raise


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_fails_open_by_default_when_redis_is_down(self):
        """Availability over strict enforcement: a Redis outage shouldn't take the
        whole API down with it."""

        limiter = RateLimiter(_ExplodingRedis(), fail_open=True)
        assert await limiter.check(key="user:1:requests", limit=10) is True

    @pytest.mark.asyncio
    async def test_can_be_configured_to_fail_closed(self):
        """For deployments where exceeding the limit is worse than being down."""

        limiter = RateLimiter(_ExplodingRedis(), fail_open=False)
        assert await limiter.check(key="user:1:requests", limit=10) is False
