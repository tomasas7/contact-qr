"""Application settings, read exclusively from the environment (or a local .env file).

Nothing secret has a default value: the database password must be supplied by the
environment in every deployed setting.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field("development", description="development | test | production")
    # Set at image build time (version + git commit); reported by /health.
    app_version: str = "dev"
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "contactqr"
    postgres_password: str = ""
    postgres_db: str = "contactqr"

    # Region used to interpret phone numbers written without a +country prefix.
    default_phone_region: str = "LT"

    # Request logs older than this are deleted by the retention job. 0 disables retention.
    log_retention_days: int = Field(90, ge=0)
    log_retention_interval_hours: float = Field(24, gt=0)

    # Per-IP limit for write requests (create/update/delete). 0 disables rate limiting.
    rate_limit_writes_per_minute: int = Field(30, ge=0)

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password, unlike string formatting.
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password or None,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
