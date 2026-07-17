from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    anthropic_api_key: str
    supabase_url: str
    supabase_publishable_key: str
    supabase_secret_key: str
    database_url: str


settings = Settings()
