from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ae_orchestrator"
    redis_url: str = "redis://localhost:6379/0"
    mcp_server_url: str = "http://localhost:3000"
    secret_key: str = "change-me-in-production"
    cors_origins: list[str] = ["http://localhost:8080"]

    model_config = {"env_prefix": "ORCHESTRATOR_", "env_file": ".env"}


settings = Settings()
