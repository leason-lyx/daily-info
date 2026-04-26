from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    database_url: str = "sqlite:///./data/daily-info.db"
    public_app_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    rsshub_public_instances: str = (
        "https://rsshub.rssforever.com,"
        "https://rsshub.ktachibana.party,"
        "https://rsshub.cups.moe,"
        "https://rsshub-balancer.virworks.moe"
    )
    rsshub_self_hosted_base_url: str | None = None

    llm_provider_type: Literal["none", "openai_compatible", "codex_cli"] = "none"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model_name: str | None = None
    llm_temperature: float = 0.2
    llm_timeout: int = 60

    codex_cli_path: str = "codex"
    codex_cli_model: str | None = None

    worker_sleep_seconds: float = 2.0
    scheduler_sleep_seconds: float = 60.0
    worker_max_job_runtime_seconds: int = 300

    @property
    def rsshub_instances(self) -> list[str]:
        configured = [x.strip().rstrip("/") for x in self.rsshub_public_instances.split(",") if x.strip()]
        if self.rsshub_self_hosted_base_url:
            configured.insert(0, self.rsshub_self_hosted_base_url.rstrip("/"))
        return configured

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider_type == "openai_compatible":
            return bool(self.llm_base_url and self.llm_api_key and self.llm_model_name)
        if self.llm_provider_type == "codex_cli":
            return bool(self.codex_cli_path)
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
