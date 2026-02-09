import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    poll_interval_seconds: float = 5.0
    batch_size: int = 10
    max_retries: int = 3
    health_port: int = 8081
    log_format: str = "json"

    @classmethod
    def from_env(cls) -> "Config":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL must be set")

        return cls(
            database_url=database_url,
            poll_interval_seconds=float(os.environ.get("KURA_POLL_INTERVAL", "5.0")),
            batch_size=int(os.environ.get("KURA_BATCH_SIZE", "10")),
            max_retries=int(os.environ.get("KURA_MAX_RETRIES", "3")),
            health_port=int(os.environ.get("KURA_HEALTH_PORT", "8081")),
            log_format=os.environ.get("KURA_LOG_FORMAT", "json"),
        )
