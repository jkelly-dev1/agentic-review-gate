"""Application configuration via pydantic-settings.

Everything defaults to a fully offline posture: AGENT_PROVIDER=mock, no webhook
secret required for local runs (the demo supplies one), and Langfuse disabled.
Set the corresponding env vars (see .env.example) to light up the real paths.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Agent / LLM ---------------------------------------------------------
    # "mock" (default, offline, deterministic), "anthropic", or "openai".
    agent_provider: str = "mock"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    # Per-provider default models (both overridable via env). agent_model is an
    # optional universal override: when set it wins for whichever provider runs.
    anthropic_model: str = "claude-opus-4-8"
    openai_model: str = "gpt-4o"
    agent_model: str | None = None

    def model_for(self, provider: str) -> str:
        """Resolve the model id for a provider (agent_model override wins)."""
        if self.agent_model:
            return self.agent_model
        return {"anthropic": self.anthropic_model, "openai": self.openai_model}.get(
            provider, self.anthropic_model
        )

    # --- GitHub webhook security --------------------------------------------
    # HMAC-SHA256 shared secret. If unset, signature verification still runs but
    # will reject everything; the demo and tests supply their own secret.
    github_webhook_secret: str = "dev-only-insecure-secret-change-me"
    # Replay-guard TTL: how long a delivery id is remembered (seconds).
    replay_ttl_seconds: int = 600

    # --- Langfuse (LLM observability) ---------------------------------------
    # When these are all set, tracing.py emits real spans; otherwise it degrades
    # to a local, in-memory no-op tracer that still records step timing.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    # Region matters: EU is https://cloud.langfuse.com, US is
    # https://us.cloud.langfuse.com. Keys authenticate against exactly one, and a
    # wrong host yields a 401 on export. Accept LANGFUSE_HOST or LANGFUSE_BASE_URL.
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("langfuse_host", "langfuse_base_url"),
    )

    # --- Rate limiting -------------------------------------------------------
    rate_limit_max_requests: int = 30
    rate_limit_window_seconds: int = 60

    # --- Eval gate -----------------------------------------------------------
    eval_pass_threshold: float = 0.80

    # --- Audit ---------------------------------------------------------------
    audit_log_path: str = "audit/audit.log.jsonl"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
