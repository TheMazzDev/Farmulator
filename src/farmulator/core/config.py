"""Settings, read from environment variables prefixed FARMULATOR_ (or a .env file)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FARMULATOR_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"

    # The API's connection. Must be the app role, so row-level security applies.
    database_url: str = (
        "postgresql+asyncpg://farmulator_app:farmulator_app@localhost:5432/farmulator"
    )
    # The migrations' connection, as the role that owns the tables.
    migration_database_url: str = (
        "postgresql+asyncpg://farmulator_owner:farmulator_owner@localhost:5432/farmulator"
    )
    app_db_role: str = "farmulator_app"

    db_echo: bool = False
    db_pool_size: int = 10

    # Development sign-in through the X-Tenant-ID header. Never in production.
    dev_auth_enabled: bool = True

    default_page_size: int = 50
    max_page_size: int = 200

    @model_validator(mode="after")
    def _no_dev_auth_in_production(self) -> Self:
        if self.environment == "production" and self.dev_auth_enabled:
            raise ValueError("dev_auth_enabled must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
