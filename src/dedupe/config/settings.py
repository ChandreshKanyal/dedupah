from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (prefix DEDUPE_), never hard-coded paths."""

    model_config = SettingsConfigDict(env_prefix="DEDUPE_", env_file=".env", extra="ignore")

    data_dir: str = "data"
    database_url: str = "postgresql://dedupe:dedupe@postgres:5432/dedupe"


def get_settings() -> Settings:
    return Settings()
