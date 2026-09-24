"""Environment configuration. Secrets never appear in representations or responses."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    session_db: Path = Path("runtime/sessions.sqlite3")

    @property
    def database_path(self) -> Path:
        return self.session_db if self.session_db.is_absolute() else PROJECT_ROOT / self.session_db
