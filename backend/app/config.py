from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Sportsline Inventory App"
    home_dir: Path = Path(__file__).resolve().parents[1]

    mysql_host: str = Field(..., env="MYSQL_HOST")
    mysql_port: int = Field(3306, env="MYSQL_PORT")
    mysql_user: str = Field(..., env="MYSQL_USER")
    mysql_password: str = Field(..., env="MYSQL_PASSWORD")
    mysql_database: str = Field(..., env="MYSQL_DATABASE")

    app_mysql_host: str = Field(..., env="APP_MYSQL_HOST")
    app_mysql_port: int = Field(3306, env="APP_MYSQL_PORT")
    app_mysql_user: str = Field(..., env="APP_MYSQL_USER")
    app_mysql_password: str = Field(..., env="APP_MYSQL_PASSWORD")
    app_mysql_database: str = Field(..., env="APP_MYSQL_DATABASE")

    shipstation_api_key: str | None = Field(None, env="SHIPSTATION_API_KEY")
    shipstation_api_secret: str | None = Field(None, env="SHIPSTATION_API_SECRET")

    smtp_host: str = Field(..., env="SMTP_HOST")
    smtp_port: int = Field(..., env="SMTP_PORT")
    smtp_user: str = Field(..., env="SMTP_USER")
    smtp_password: str = Field(..., env="SMTP_PASSWORD")

    twilio_sid: str | None = Field(None, env="TWILIO_SID")
    twilio_token: str | None = Field(None, env="TWILIO_TOKEN")
    twilio_from_number: str | None = Field(None, env="TWILIO_FROM_NUMBER")

    internal_sync_token: str = Field(..., env="INTERNAL_SYNC_TOKEN")
    internal_sync_url: str = Field("http://inventory-app:8000/internal-sync", env="INTERNAL_SYNC_URL")
    poll_interval_seconds: int = Field(90, env="POLL_INTERVAL_SECONDS")
    session_secret: str = Field(..., env="SESSION_SECRET")

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
