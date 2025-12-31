"""
Application configuration using Pydantic Settings.
Loads configuration from environment variables with sensible defaults.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/jobqueue"
    database_sync_url: str = "postgresql://postgres:postgres@localhost:5432/jobqueue"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = "your-secret-key-change-in-production"
    api_algorithm: str = "HS256"
    api_access_token_expire_minutes: int = 30

    # Worker Configuration
    worker_id: str = "worker-1"
    worker_lease_duration_seconds: int = 30
    worker_poll_interval_seconds: float = 1.0
    worker_batch_size: int = 10
    worker_heartbeat_interval_seconds: float = 10.0

    # Reaper Configuration
    reaper_interval_seconds: int = 10

    # Rate Limiting
    rate_limit_requests_per_minute: int = 100

    # Tenant Defaults
    default_tenant_max_concurrent_jobs: int = 10
    default_max_attempts: int = 3

    # Observability
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "job-scheduler"
    prometheus_port: int = 9090
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    # Redis (optional, for distributed rate limiting)
    redis_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
