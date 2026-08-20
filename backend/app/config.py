"""Central configuration. All values come from the environment — never hardcode
secrets or environment-specific values elsewhere in the codebase."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Database ---
    database_url: PostgresDsn = Field(alias="DATABASE_URL")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")

    # --- Redis (rate limiting, shared across replicas — see DECISIONS.md) ---
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- Auth ---
    jwt_secret: SecretStr = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_ttl_minutes: int = Field(default=30, alias="ACCESS_TOKEN_TTL_MINUTES")
    refresh_token_ttl_days: int = Field(default=14, alias="REFRESH_TOKEN_TTL_DAYS")

    # --- Admin bootstrap (FR-A5: no self-service registration) ---
    # If set and no admin exists yet, one is created on startup so the very first
    # operator has a way in without a public signup endpoint. Safe to leave unset
    # after the first admin exists — the check is a no-op once one is found.
    bootstrap_admin_email: str | None = Field(default=None, alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: SecretStr | None = Field(default=None, alias="BOOTSTRAP_ADMIN_PASSWORD")

    # --- LLM gateway (OpenAI only per DECISIONS.md Q1) ---
    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    llm_cheap_model: str = Field(default="gpt-4o-mini", alias="LLM_CHEAP_MODEL")
    llm_strong_model: str = Field(default="gpt-4o", alias="LLM_STRONG_MODEL")
    llm_cheap_tier_max_retries: int = Field(default=1, alias="LLM_CHEAP_TIER_MAX_RETRIES")
    llm_strong_tier_max_retries: int = Field(default=3, alias="LLM_STRONG_TIER_MAX_RETRIES")
    llm_request_timeout_s: float = Field(default=60.0, alias="LLM_REQUEST_TIMEOUT_S")
    session_token_budget: int = Field(default=1_000_000, alias="SESSION_TOKEN_BUDGET")

    # --- Rate limiting ---
    rate_limit_requests_per_minute: int = Field(default=30, alias="RATE_LIMIT_RPM")
    rate_limit_messages_per_minute: int = Field(default=10, alias="RATE_LIMIT_MESSAGES_RPM")
    # Fails CLOSED by default: if Redis is unreachable, requests are rejected rather
    # than silently unlimited. Flip to true only if availability during a Redis
    # outage matters more than enforcement for a given deployment.
    rate_limit_fail_open: bool = Field(default=False, alias="RATE_LIMIT_FAIL_OPEN")

    # --- Scale envelope (see DECISIONS.md) ---
    max_components_per_model: int = Field(default=50, alias="MAX_COMPONENTS")
    max_dependencies_per_model: int = Field(default=200, alias="MAX_DEPENDENCIES")

    # --- Review / refine ---
    max_refine_iterations: int = Field(default=2, alias="MAX_REFINE_ITERATIONS")

    # --- Observability (no-op if unset, see DECISIONS.md) ---
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")

    # --- CORS ---
    # NoDecode is required: without it pydantic-settings tries to JSON-parse the env
    # value before any validator runs, so a plain comma-separated list (the format
    # documented in .env.example) would raise SettingsError at startup.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ALLOW_ORIGINS"
    )

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def _reject_unsafe_production_config(self) -> "Settings":
        """Fails fast on config that must never reach production. A weak signing key
        or a placeholder secret is a silent vulnerability otherwise — better to
        refuse to boot than to serve traffic with forgeable tokens."""

        if self.env.lower() not in ("production", "prod"):
            return self

        secret = self.jwt_secret.get_secret_value()
        problems: list[str] = []

        # HS256 keys below 32 bytes are under the RFC 7518 §3.2 minimum.
        if len(secret.encode("utf-8")) < 32:
            problems.append("JWT_SECRET must be at least 32 bytes in production")
        if "changeme" in secret.lower() or secret.lower().startswith("test"):
            problems.append("JWT_SECRET is still a placeholder value")
        if "*" in self.cors_allow_origins:
            problems.append("CORS_ALLOW_ORIGINS must not be '*' in production")

        if problems:
            raise ValueError("unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
