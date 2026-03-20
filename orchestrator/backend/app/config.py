from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ae_orchestrator"
    redis_url: str = "redis://localhost:6379/0"
    mcp_server_url: str = "http://localhost:3000"
    secret_key: str = "change-me-in-production"
    cors_origins: list[str] = ["http://localhost:8080"]

    google_api_key: str = ""
    google_project: str = ""
    google_location: str = "us-central1"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    anthropic_api_key: str = ""

    model_config = {"env_prefix": "ORCHESTRATOR_", "env_file": ".env"}


settings = Settings()
