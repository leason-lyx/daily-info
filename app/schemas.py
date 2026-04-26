from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class SourceAttemptIn(BaseModel):
    kind: str = "direct"
    adapter: str = "feed"
    url: str = ""
    route: str = ""
    priority: int = 0
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class SourceIn(BaseModel):
    id: str
    name: str
    content_type: Literal["paper", "blog", "post"]
    platform: str = ""
    homepage_url: str = ""
    enabled: bool = False
    group: str = "General"
    priority: int = 100
    poll_interval: int = 3600
    auto_summary_enabled: bool | None = None
    auto_summary_days: int = Field(default=7, ge=1)
    language_hint: str = "auto"
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    default_tags: list[str] = Field(default_factory=list)
    attempts: list[SourceAttemptIn] = Field(default_factory=list)
    fulltext: dict[str, Any] = Field(default_factory=lambda: {"strategy": "feed_field"})
    auth_mode: str = "none"
    stability_level: str = "stable"

    @model_validator(mode="after")
    def default_auto_summary_enabled(self):
        if self.auto_summary_enabled is None:
            self.auto_summary_enabled = self.content_type in {"blog", "post"}
        return self


class SourcePatch(BaseModel):
    name: str | None = None
    content_type: Literal["paper", "blog", "post"] | None = None
    platform: str | None = None
    homepage_url: str | None = None
    enabled: bool | None = None
    group: str | None = None
    priority: int | None = None
    poll_interval: int | None = None
    auto_summary_enabled: bool | None = None
    auto_summary_days: int | None = Field(default=None, ge=1)
    language_hint: str | None = None
    include_keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    default_tags: list[str] | None = None
    attempts: list[SourceAttemptIn] | None = None
    fulltext: dict[str, Any] | None = None
    auth_mode: str | None = None
    stability_level: str | None = None


class SourceAttemptOut(SourceAttemptIn):
    id: int | None = None


class SourceOut(SourceIn):
    auto_summary_enabled: bool = False
    auto_summary_days: int = 7
    is_builtin: bool = False
    attempts: list[SourceAttemptOut] = Field(default_factory=list)
    latest_run: dict[str, Any] | None = None
    content_audit: dict[str, Any] = Field(default_factory=dict)


class PreviewRequest(BaseModel):
    url: str | None = None
    route: str | None = None
    adapter: str = "feed"
    content_type: Literal["paper", "blog", "post"] = "blog"


class PreviewEntry(BaseModel):
    title: str
    url: str
    published_at: datetime | None = None
    authors: list[str] = Field(default_factory=list)
    summary: str = ""
    has_text: bool = False


class PreviewResponse(BaseModel):
    detected_adapter: str
    entries: list[PreviewEntry]
    warnings: list[str] = Field(default_factory=list)
    used_url: str | None = None


class ItemOut(BaseModel):
    id: str
    source_id: str
    source_name: str
    content_type: str
    platform: str
    title: str
    chinese_title: str = ""
    url: str
    authors: list[str]
    published_at: datetime | None
    summary: str = ""
    raw_text: str = ""
    ai_summary: dict[str, Any] | None = None
    tags: list[str]
    entities: list[str]
    read: bool
    starred: bool
    hidden: bool
    summary_status: str


class ItemListOut(BaseModel):
    items: list[ItemOut]
    total: int


class SettingsOut(BaseModel):
    database_url: str
    rsshub_public_instances: list[str]
    rsshub_self_hosted_base_url: str | None = None
    llm_provider_type: str
    llm_configured: bool
    llm_base_url: str | None = None
    llm_model_name: str | None = None
    codex_cli_path: str
    codex_cli_model: str | None = None
    llm_usage: dict[str, Any] = Field(default_factory=dict)


class SettingsPatch(BaseModel):
    llm_provider_type: Literal["none", "openai_compatible", "codex_cli"] | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model_name: str | None = None
    llm_temperature: float | None = None
    llm_timeout: int | None = None
    codex_cli_path: str | None = None
    codex_cli_model: str | None = None


class AiProviderTestResult(BaseModel):
    ok: bool
    provider: str
    model: str | None = None
    duration_ms: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
