from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://parcella:changeme@localhost:5432/parcella"

    # Security
    secret_key: str = "dev-secret-key-change-in-production"
    session_max_age: int = 60 * 60 * 8  # 8 hours

    # Environment
    environment: str = "development"

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@parcella.local"
    smtp_tls: bool = True

    # App metadata
    app_name: str = "Parcella"
    app_version: str = "0.1.0"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user)

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
