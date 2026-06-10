import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "CapabilityOS"
    version: str = "1.0.0"
    database_url: str = os.environ.get(
        "CAPOS_DATABASE_URL", "sqlite:///./capabilityos.db"
    )
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("CAPOS_LLM_MODEL", "claude-sonnet-4-6")
    # Separate judge model per PRD 8.3: judge model cannot be the generation model.
    judge_model: str = os.environ.get("CAPOS_JUDGE_MODEL", "claude-opus-4-8")
    default_tenant_slug: str = "demo"

    class Config:
        env_prefix = "CAPOS_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
