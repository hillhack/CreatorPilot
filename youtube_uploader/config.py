"""Configuration settings management using Pydantic."""

from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Google / YouTube OAuth & API settings
    credentials_file: str = "credentials.json"
    token_file: str = "token.json"
    redirect_uri: str = "http://localhost:8080/"
    youtube_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    scopes: List[str] = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    # Upload defaults
    default_privacy_status: str = "private"  # 'private', 'unlisted', or 'public'
    default_category_id: str = "22"  # Category 22 = People & Blogs
    chunk_size_mb: int = 4  # Resumable upload chunk size in MB (must be multiple of 256KB)

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000

    # Development & Dry-Run flags
    mock_youtube_api: bool = False


settings = Settings()
