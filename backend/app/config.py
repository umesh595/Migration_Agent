"""Central configuration. All values come from the environment — never hardcode
secrets or environment-specific values elsewhere in the codebase."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

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

    # --- LLM gateway (CodeVector/Fision Labs Kimi primary; Gemini optional fallback) ---
    codevector_api_key: SecretStr | None = Field(default=None, alias="CODEVECTOR_API_KEY")
    codevector_base_url: str | None = Field(default=None, alias="CODEVECTOR_BASE_URL")
    codevector_cheap_model: str = Field(default="kimi-k2", alias="CODEVECTOR_CHEAP_MODEL")
    codevector_strong_model: str = Field(default="kimi-k2", alias="CODEVECTOR_STRONG_MODEL")
    # Accepted aliases for the same office gateway, so different local .env files
    # do not need code changes.
    fision_labs_api_key: SecretStr | None = Field(default=None, alias="FISION_LABS_API_KEY")
    fision_labs_base_url: str | None = Field(default=None, alias="FISION_LABS_BASE_URL")
    fision_labs_kimi_model: str | None = Field(default=None, alias="FISION_LABS_KIMI_MODEL")
    kimi_api_key: SecretStr | None = Field(default=None, alias="KIMI_API_KEY")
    kimi_base_url: str | None = Field(default=None, alias="KIMI_BASE_URL")
    kimi_model: str | None = Field(default=None, alias="KIMI_MODEL")
    # Kept so old .env files continue to load while deployments migrate providers.
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_cheap_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_CHEAP_MODEL")
    anthropic_strong_model: str = Field(default="claude-opus-5", alias="ANTHROPIC_STRONG_MODEL")
    # Only required for an identity-linked API key (one generated from a personal
    # Console profile rather than from inside a specific workspace's own API Keys
    # tab) — such a key can't infer which workspace's budget to bill against, so
    # every request must declare it explicitly. A key generated from within a
    # workspace's API Keys tab is already bound to that workspace and needs this
    # unset.
    anthropic_workspace_id: str | None = Field(default=None, alias="ANTHROPIC_WORKSPACE_ID")
    # Kept only so old .env files don't fail validation while deployments migrate.
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    llm_cheap_model: str = Field(default="claude-sonnet-5", alias="LLM_CHEAP_MODEL")
    llm_strong_model: str = Field(default="claude-opus-5", alias="LLM_STRONG_MODEL")
    llm_cheap_tier_max_retries: int = Field(default=1, alias="LLM_CHEAP_TIER_MAX_RETRIES")
    llm_strong_tier_max_retries: int = Field(default=3, alias="LLM_STRONG_TIER_MAX_RETRIES")
    llm_request_timeout_s: float = Field(default=60.0, alias="LLM_REQUEST_TIMEOUT_S")
    codevector_response_format: Literal["json_schema", "json_object"] = Field(
        default="json_schema", alias="CODEVECTOR_RESPONSE_FORMAT"
    )
    codevector_fallback_model: str | None = Field(default=None, alias="CODEVECTOR_FALLBACK_MODEL")
    llm_call_timeout_s: float = Field(default=210.0, gt=0, alias="LLM_CALL_TIMEOUT_S")
    llm_critic_timeout_s: float = Field(default=60.0, gt=0, alias="LLM_CRITIC_TIMEOUT_S")
    discovery_fast_mode: bool = Field(default=True, alias="DISCOVERY_FAST_MODE")
    discovery_full_prompt_min_chars: int = Field(default=3_500, ge=1, alias="DISCOVERY_FULL_PROMPT_MIN_CHARS")
    session_token_budget: int = Field(default=1_000_000, alias="SESSION_TOKEN_BUDGET")

    # --- Gemini: optional fallback only, used when CodeVector's API/account is unavailable. ---
    google_ai_studio_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_AI_STUDIO_API_KEY")
    google_ai_studio_cheap_model: str = Field(default="gemini-2.5-flash", alias="GOOGLE_AI_STUDIO_CHEAP_MODEL")
    google_ai_studio_strong_model: str = Field(default="gemini-2.5-pro", alias="GOOGLE_AI_STUDIO_STRONG_MODEL")

    # --- Groq: legacy optional fallback settings; not wired in main.py now. ---
    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY")
    # Groq's hosted catalog changes over time and varies by account — verified
    # directly against this project's own Groq account before picking these
    # (see DECISIONS.md's model-verification note for why that's the
    # standard here, not assumed from a model's name or release notes).
    groq_cheap_model: str = Field(default="openai/gpt-oss-20b", alias="GROQ_CHEAP_MODEL")
    groq_strong_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_STRONG_MODEL")

    @property
    def active_codevector_api_key(self) -> SecretStr | None:
        return self.codevector_api_key or self.fision_labs_api_key or self.kimi_api_key

    @property
    def active_codevector_base_url(self) -> str | None:
        return self.codevector_base_url or self.fision_labs_base_url or self.kimi_base_url

    @property
    def active_codevector_cheap_model(self) -> str:
        return self.fision_labs_kimi_model or self.kimi_model or self.codevector_cheap_model

    @property
    def active_codevector_strong_model(self) -> str:
        return self.fision_labs_kimi_model or self.kimi_model or self.codevector_strong_model

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

    # --- Enterprise catalog integration (optional, no-op if unset — same
    # pattern as Langfuse above: the feature is simply absent, not broken,
    # on a deployment that never configured it) ---
    catalog_base_url: str | None = Field(default=None, alias="CATALOG_BASE_URL")
    catalog_token_url: str | None = Field(default=None, alias="CATALOG_TOKEN_URL")
    catalog_client_id: str | None = Field(default=None, alias="CATALOG_CLIENT_ID")
    catalog_client_secret: SecretStr | None = Field(default=None, alias="CATALOG_CLIENT_SECRET")
    catalog_request_timeout_s: float = Field(default=10.0, alias="CATALOG_REQUEST_TIMEOUT_S")

    # --- Cost estimation (optional, no-op if unset — same pattern as Langfuse/
    # catalog above: AWS/Azure pricing works with no configuration; GCP components
    # are reported unestimated rather than guessed until this is set) ---
    gcp_billing_api_key: SecretStr | None = Field(default=None, alias="GCP_BILLING_API_KEY")

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
