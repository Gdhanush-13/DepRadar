from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./depradar.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "dev-secret-change-me"
    github_token: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    app_base_url: str = "http://localhost:8000"
    internal_rescan_key: str = "dev-internal-key-change-me"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
