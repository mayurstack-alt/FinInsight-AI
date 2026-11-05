"""Typed, environment-based application settings.

Design rules
------------
- Public (browser-safe) keys live in ``Settings``.
- Server-only credentials that bypass RLS or access storage are validated via
  ``require_service_role_key()``; they are never read in route handlers and
  should never be serialised to logs or API responses.
"""

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

# Placeholder values copied verbatim from .env.example. If the loaded value
# matches one of these we treat the setting as unconfigured.
_PLACEHOLDER_PREFIXES = (
    "your-",
    "sb_publishable_your",
    "https://your-project",
    "postgresql+psycopg://postgres:postgres@localhost",
    "postgresql+psycopg://postgres:password@db.example",
)


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    return any(value.startswith(p) for p in _PLACEHOLDER_PREFIXES)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_BASE_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # App identity                                                          #
    # ------------------------------------------------------------------ #
    app_name: str = "FinInsight AI"
    api_v1_prefix: str = "/api/v1"

    # ------------------------------------------------------------------ #
    # Supabase — public / publishable (safe to use in browser-visible code) #
    # ------------------------------------------------------------------ #
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None

    # ------------------------------------------------------------------ #
    # Supabase — server-only (bypasses RLS; NEVER log or return in a      #
    # response body)                                                        #
    # ------------------------------------------------------------------ #
    supabase_service_role_key: str | None = None

    # ------------------------------------------------------------------ #
    # Database                                                              #
    # ------------------------------------------------------------------ #
    # Default is an obviously-local value. The app warns loudly at startup
    # when this default is in use so developers don't silently hit the
    # wrong database.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/fininsight"

    # ------------------------------------------------------------------ #
    # PDF processing                                                        #
    # ------------------------------------------------------------------ #
    max_pdf_size_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    pdf_processing_max_pages: int = Field(default=500, gt=0, le=5000)
    pdf_text_min_characters: int = Field(default=35, ge=1, le=1000)
    pdf_ocr_dpi: int = Field(default=200, ge=100, le=400)
    pdf_ocr_timeout_seconds: int = Field(default=30, ge=1, le=180)

    # ------------------------------------------------------------------ #
    # Storage / downloads                                                   #
    # ------------------------------------------------------------------ #
    report_download_url_ttl_seconds: int = Field(default=300, ge=60, le=900)

    # ------------------------------------------------------------------ #
    # Infrastructure                                                        #
    # ------------------------------------------------------------------ #
    redis_url: str = "redis://localhost:6379/0"
    frontend_origins: str = "http://localhost:5173"
    tesseract_cmd: str | None = None

    # ------------------------------------------------------------------ #
    # Validators                                                            #
    # ------------------------------------------------------------------ #

    @field_validator("supabase_url", "supabase_publishable_key", mode="before")
    @classmethod
    def _reject_placeholder_public(cls, v: str | None) -> str | None:
        """Treat .env.example placeholder values as absent."""
        return None if _is_placeholder(v) else v

    @field_validator("supabase_service_role_key", mode="before")
    @classmethod
    def _reject_placeholder_secret(cls, v: str | None) -> str | None:
        return None if _is_placeholder(v) else v

    @field_validator("database_url", mode="before")
    @classmethod
    def _warn_placeholder_db(cls, v: str) -> str:
        if _is_placeholder(v):
            logger.warning(
                "DATABASE_URL looks like a placeholder or localhost default. "
                "Set DATABASE_URL in backend/.env to your Supabase PostgreSQL "
                "connection string before using database features."
            )
        return v

    @model_validator(mode="after")
    def _warn_unconfigured_supabase(self) -> "Settings":
        if not self.supabase_url:
            logger.warning(
                "SUPABASE_URL is not set. Authentication will not work until "
                "SUPABASE_URL is configured in backend/.env."
            )
        if not self.supabase_publishable_key:
            logger.warning(
                "SUPABASE_PUBLISHABLE_KEY is not set. HS256 token verification "
                "and the frontend client will not work."
            )
        return self

    # ------------------------------------------------------------------ #
    # Computed properties                                                   #
    # ------------------------------------------------------------------ #

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def supabase_storage_configured(self) -> bool:
        """True only when both server-only storage credentials are present."""
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def database_url_is_default(self) -> bool:
        """True when the app is using the localhost fallback DATABASE_URL."""
        return _is_placeholder(self.database_url)

    def require_service_role_key(self) -> str:
        """Return the service-role key or raise RuntimeError.

        Call this only inside server-side service code — never in route
        handlers and never in a context that could be serialised.
        """
        if not self.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY is required for this operation but is "
                "not configured. Set it in backend/.env."
            )
        return self.supabase_service_role_key


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
