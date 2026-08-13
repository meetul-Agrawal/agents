from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    nvidia_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "nvidia/llama-3.1-nemotron-70b-instruct"

    mongodb_uri: str = "mongodb://localhost:27017/"
    mongodb_database: str = "sf_tenant_6a33b5b2091da2fb4a7c3de4"

    log_level: str = "INFO"
    nim_rate_limit_rpm: int = 40


@lru_cache
def get_settings() -> Settings:
    return Settings()
