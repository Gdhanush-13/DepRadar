from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SCAN_RATE_LIMIT = 10
DEFAULT_SCAN_RATE_WINDOW_SECONDS = 600


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./depradar.db"
    jwt_secret: str = "dev-secret-change-me"
    depradar_env: str = "development"
    github_token: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    app_base_url: str = "http://localhost:8000"
    internal_rescan_key: str = "dev-internal-key-change-me"
    scan_rate_limit_per_window: int = DEFAULT_SCAN_RATE_LIMIT
    scan_rate_window_seconds: int = DEFAULT_SCAN_RATE_WINDOW_SECONDS
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def validate_production_config() -> None:
    if settings.depradar_env.lower() != "production":
        return
    placeholders = {
        "JWT_SECRET": settings.jwt_secret,
        "INTERNAL_RESCAN_KEY": settings.internal_rescan_key,
    }
    invalid = [name for name, value in placeholders.items() if not value or "change-me" in value or "replace-with" in value]
    if invalid:
        raise RuntimeError("Production configuration is invalid: set " + ", ".join(invalid))
