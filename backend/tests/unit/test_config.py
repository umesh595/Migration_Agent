"""Config safety tests.

The production guard exists because a weak signing key or wildcard CORS is a silent
vulnerability — the app should refuse to boot rather than serve forgeable tokens.
The CORS parsing test exists because pydantic-settings JSON-decodes list fields
before validators run, which made the documented comma-separated format crash at
startup until NoDecode was applied.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

_BASE = {
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
    "OPENAI_API_KEY": "sk-test",
}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


def test_comma_separated_cors_origins_parse_without_json_decoding():
    settings = _settings(JWT_SECRET="x" * 40, CORS_ALLOW_ORIGINS="http://a.com, http://b.com")
    assert settings.cors_allow_origins == ["http://a.com", "http://b.com"]


def test_single_cors_origin_parses():
    settings = _settings(JWT_SECRET="x" * 40, CORS_ALLOW_ORIGINS="http://localhost:3000")
    assert settings.cors_allow_origins == ["http://localhost:3000"]


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        _settings(APP_ENV="production", JWT_SECRET="tooshort")


def test_production_rejects_placeholder_jwt_secret():
    with pytest.raises(ValidationError, match="placeholder"):
        _settings(APP_ENV="production", JWT_SECRET="changeme-generate-a-real-secret-0000")


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError, match="must not be"):
        _settings(APP_ENV="production", JWT_SECRET="x" * 40, CORS_ALLOW_ORIGINS="*")


def test_production_accepts_safe_config():
    settings = _settings(
        APP_ENV="production", JWT_SECRET="x" * 40, CORS_ALLOW_ORIGINS="https://app.example.com"
    )
    assert settings.env == "production"


def test_development_tolerates_weak_secret_for_local_convenience():
    settings = _settings(APP_ENV="development", JWT_SECRET="short")
    assert settings.env == "development"
