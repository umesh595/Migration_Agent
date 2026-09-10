"""Focused error classification tests for the live AWS inventory connector."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from app.integrations.aws_provider import AWSAuthenticationError, AWSCredentials, _fetch_sync


def test_invalid_aws_api_credentials_get_a_safe_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class STS:
        def get_caller_identity(self) -> None:
            raise ClientError({"Error": {"Code": "AuthFailure", "Message": "denied"}}, "GetCallerIdentity")

    class Session:
        def client(self, service_name: str) -> STS:
            assert service_name == "sts"
            return STS()

    monkeypatch.setattr("app.integrations.aws_provider.boto3.Session", lambda **_kwargs: Session())

    with pytest.raises(AWSAuthenticationError, match="Console username/password cannot be used"):
        _fetch_sync(AWSCredentials(access_key_id="not-a-real-key", secret_access_key="not-a-real-secret"))
