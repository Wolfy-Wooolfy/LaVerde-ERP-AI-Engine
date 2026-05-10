from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "CRM AI Engine"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # ── Odoo ─────────────────────────────────────────────────────────────────
    ODOO_URL: str
    ODOO_DB: str
    ODOO_USERNAME: str
    ODOO_API_KEY: str
    ODOO_TIMEOUT_SECONDS: int = 30
    ODOO_MAX_RETRIES: int = 3

    # ── Auth ──────────────────────────────────────────────────────────────────
    BASIC_AUTH_USERNAME: str
    BASIC_AUTH_PASSWORD: str

    # ── Cache ─────────────────────────────────────────────────────────────────
    CACHE_TTL_SECONDS: int = 60

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = []

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_SUMMARY: str = "30/minute"
    RATE_LIMIT_FOLLOWUP: str = "30/minute"
    RATE_LIMIT_HTML: str = "60/minute"
    RATE_LIMIT_HEALTH: str = "600/minute"

    # ── Security headers ──────────────────────────────────────────────────────
    CSP_POLICY: str = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )

    # ── CRM Stage IDs (stored as comma-separated strings for .env compat) ────
    CRM_CRITICAL_STAGE_IDS: str = "28,34,35,37,41"
    CRM_CLOSED_EXCLUDED_STAGE_IDS: str = "26,30,31,32,38,42,46"
    CRM_DATA_QUALITY_STAGE_IDS: str = "44"

    # ── Derived stage ID lists ────────────────────────────────────────────────
    @property
    def critical_stage_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.CRM_CRITICAL_STAGE_IDS.split(",") if x.strip()]

    @property
    def closed_excluded_stage_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.CRM_CLOSED_EXCLUDED_STAGE_IDS.split(",") if x.strip()]

    @property
    def data_quality_stage_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.CRM_DATA_QUALITY_STAGE_IDS.split(",") if x.strip()]

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return upper

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        lower = v.lower()
        if lower not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}")
        return lower


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings: Settings = get_settings()
