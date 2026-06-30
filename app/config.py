"""Application configuration, env-driven with sane defaults.

All settings can be overridden via environment variables (prefix ``WATCHDOG_``)
or a local ``.env`` file. The app is designed to run with zero configuration.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WATCHDOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---
    db_path: str = "data/watchdog.db"

    # --- Detection ---
    bucket_seconds: int = 10
    # z=6 keeps single-bucket noise from bursty discrete features (e.g.
    # error_rate) below the alert threshold while real incidents (z in the
    # hundreds) still flag every bucket. See README "Tuning" for the analysis.
    z_threshold: float = 6.0
    iforest_warmup: int = 30
    alert_threshold: float = 0.7

    # --- Alerting ---
    webhook_url: str = "http://localhost:8001/webhook"
    alert_cooldown_seconds: int = 30

    # --- Synthetic generator ---
    generator: bool = True

    # --- LLM layer (feature-flagged GenAI showcase) ---
    llm_enabled: bool = False
    llm_model: str = "claude-haiku-4-5"

    # Read without the WATCHDOG_ prefix so the standard Anthropic env var works.
    anthropic_api_key: str | None = None

    @property
    def llm_active(self) -> bool:
        """LLM enrichment only runs when explicitly enabled AND a key is present."""
        return self.llm_enabled and bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    # Allow the conventional ANTHROPIC_API_KEY (no prefix) to flow in.
    import os

    s = Settings()
    if s.anthropic_api_key is None and os.getenv("ANTHROPIC_API_KEY"):
        s.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    return s
