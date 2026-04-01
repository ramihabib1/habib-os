"""
Central settings module — loads all config from .env via pydantic-settings.
Import `settings` anywhere in the codebase; never read os.environ directly.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # ── Anthropic ────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = Field(..., description="Anthropic API key")

    # ── Supabase ─────────────────────────────────────────────────────────────
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_SERVICE_KEY: str = Field(..., description="Service role key — bypasses RLS")
    SUPABASE_ANON_KEY: str = Field(..., description="Anon key — RLS enforced (dashboard)")

    # ── Amazon SP-API ─────────────────────────────────────────────────────────
    SP_API_CLIENT_ID: str = Field(..., description="LWA OAuth client ID")
    SP_API_CLIENT_SECRET: str = Field(..., description="LWA OAuth client secret")
    SP_API_REFRESH_TOKEN: str = Field(..., description="LWA refresh token")
    SP_API_AWS_ACCESS_KEY: str = Field(..., description="AWS IAM access key")
    SP_API_AWS_SECRET_KEY: str = Field(..., description="AWS IAM secret key")
    SP_API_ROLE_ARN: str = Field(
        default="arn:aws:iam::104981180708:role/habib-spapi-role",
        description="AWS role ARN for SP-API STS AssumeRole",
    )
    SP_API_MARKETPLACE_CA: str = Field(
        default="A2EUQ1WTGCTBG2", description="Amazon Canada marketplace ID"
    )
    SP_API_MARKETPLACE_US: str = Field(
        default="ATVPDKIKX0DER", description="Amazon US marketplace ID (future)"
    )

    # ── Amazon Advertising API ────────────────────────────────────────────────
    ADS_API_CLIENT_ID: str = Field(..., description="Advertising API OAuth client ID")
    ADS_API_CLIENT_SECRET: str = Field(..., description="Advertising API OAuth client secret")
    ADS_API_REFRESH_TOKEN: str = Field(..., description="Advertising API refresh token")
    ADS_API_PROFILE_ID: str = Field(..., description="Advertising API profile ID")

    # ── Telegram ──────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = Field(..., description="Telegram bot token from BotFather")
    TELEGRAM_RAMI_CHAT_ID: str = Field(..., description="Rami's Telegram chat ID")
    TELEGRAM_FATHER_CHAT_ID: str = Field(..., description="Father's Telegram chat ID")
    TELEGRAM_MAREE_CHAT_ID: str = Field(..., description="Maree's Telegram chat ID")

    # ── System ────────────────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    ENVIRONMENT: str = Field(default="development", description="development | production")

    # ── Derived constants (not from .env) ─────────────────────────────────────
    SP_API_BASE_URL: str = "https://sellingpartnerapi-na.amazon.com"
    ADS_API_BASE_URL: str = "https://advertising-api.amazon.com"
    LWA_TOKEN_URL: str = "https://api.amazon.com/auth/o2/token"
    APPROVAL_EXPIRY_HOURS: int = 24
    COST_BUDGET_MONTHLY_USD: float = 20.0
    # Known active ASIN used as a probe for seller ID discovery via fees API.
    # Any stable active Anabtawi ASIN works — Almond Fingers 375g.
    PROBE_ASIN: str = "B0FT3HN2XV"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()


settings = get_settings()
