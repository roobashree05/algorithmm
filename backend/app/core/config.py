from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_name: str = "bugtracker"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://app:app@postgres:5432/bugtracker"
    redis_url: str = ""

    whatsapp_provider: Literal["mock", "cloud_api"] = "mock"
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_groups_enabled: bool = False

    gitlab_provider: Literal["mock", "rest"] = "mock"
    gitlab_base_url: str = ""
    gitlab_access_token: str = ""
    gitlab_webhook_secret: str = ""
    gitlab_timeout_seconds: float = 10.0
    gitlab_mock_state_path: str = ""

    attachment_dir: str = "/data/attachments"
    attachment_max_bytes: int = 5_000_000
    attachment_retention_hours: int = 24
    conversation_ttl_minutes: int = 120
    notification_max_attempts: int = 5
    notification_backoff_base_seconds: float = 2.0
    notification_backoff_cap_seconds: float = 300.0
    outbox_poll_seconds: float = 2.0
    enable_outbox_worker: bool = True
    rate_limit_per_minute: int = 120
    seed_demo_data: bool = True
    default_labels: str = "bug,whatsapp,customer-reported"
    cors_origins: str = "http://localhost:8080,http://127.0.0.1:8080"

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "Settings":
        if self.whatsapp_provider == "cloud_api":
            required = {
                "WHATSAPP_ACCESS_TOKEN": self.whatsapp_access_token,
                "WHATSAPP_PHONE_NUMBER_ID": self.whatsapp_phone_number_id,
                "WHATSAPP_APP_SECRET": self.whatsapp_app_secret,
                "WHATSAPP_VERIFY_TOKEN": self.whatsapp_verify_token,
                "WHATSAPP_API_VERSION": self.whatsapp_api_version,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("Missing WhatsApp configuration: " + ", ".join(missing))
        if self.gitlab_provider == "rest":
            required = {
                "GITLAB_BASE_URL": self.gitlab_base_url,
                "GITLAB_ACCESS_TOKEN": self.gitlab_access_token,
                "GITLAB_WEBHOOK_SECRET": self.gitlab_webhook_secret,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("Missing GitLab configuration: " + ", ".join(missing))
        return self

    @property
    def label_defaults(self) -> list[str]:
        return [part.strip() for part in self.default_labels.split(",") if part.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
