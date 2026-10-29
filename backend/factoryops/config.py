"""Application configuration loaded from environment variables (prefix FACTORYOPS_)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FACTORYOPS_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://localhost:5432/factoryops"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "factoryops.telemetry"
    kafka_dlq_topic: str = "factoryops.telemetry.dlq"
    kafka_group_id: str = "factoryops-consumer"

    # Ingestion policy
    late_event_seconds: int = 120  # event older than this when received => flagged late
    max_event_age_seconds: int = 7 * 24 * 3600  # older than this => rejected
    max_clock_skew_seconds: int = 300  # event timestamp this far in the future => rejected
    alert_cooldown_seconds: int = 300  # suppress re-open of a resolved alert key for this long
    processing_max_retries: int = 5

    # Demo controls exposed through the API (disable outside local/demo deployments)
    demo_controls_enabled: bool = True

    # Live notification channel (Postgres LISTEN/NOTIFY)
    notify_channel: str = "factoryops_live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
