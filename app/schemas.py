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


class FetchAttemptIn(BaseModel):
    adapter: Literal["feed", "rsshub", "html_index"]
    url: str = ""
    route: str = ""
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    selectors: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)

    @model_validator(mode="after")
    def require_location(self):
        if self.adapter in {"feed", "html_index"} and not self.url:
            raise ValueError(f"{self.adapter} attempt requires url")
        if self.adapter == "rsshub" and not (self.route or self.url):
            raise ValueError("rsshub attempt requires route or url")
        return self


class FetchConfigIn(BaseModel):
    strategy: Literal["first_success"] = "first_success"
    interval_seconds: int = Field(default=3600, ge=60)
    attempts: list[FetchAttemptIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_attempts(self):
        if not self.attempts:
            raise ValueError("fetch.attempts must contain at least one attempt")
        return self


class FulltextPolicyIn(BaseModel):
    mode: Literal["feed_only", "detail_only", "feed_then_detail"] = "feed_only"
    min_feed_chars: int = Field(default=1200, ge=0)
    max_detail_pages_per_run: int = Field(default=20, ge=0)
    selectors: list[str] = Field(default_factory=list)
    remove_selectors: list[str] = Field(default_factory=list)
    min_detail_chars: int = Field(default=200, ge=0)


class SummaryPolicyIn(BaseModel):
    auto: bool = False
    window_days: int = Field(default=7, ge=1)


class AuthPolicyIn(BaseModel):
    mode: str = "none"
    secret_ref: str = ""


class ProcessingFiltersIn(BaseModel):
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class SourceDefinitionIn(BaseModel):
    id: str
    title: str
    kind: Literal["paper", "blog", "post"]
    platform: str = ""
    homepage: str = ""
    language: str = "auto"
    tags: list[str] = Field(default_factory=list)
    group: str = "General"
    priority: int = 100
    fetch: FetchConfigIn
    fulltext: FulltextPolicyIn = Field(default_factory=FulltextPolicyIn)
    summary: SummaryPolicyIn | None = None
    filters: ProcessingFiltersIn = Field(default_factory=ProcessingFiltersIn)
    auth: AuthPolicyIn = Field(default_factory=AuthPolicyIn)
    stability: str = "stable"

    @model_validator(mode="after")
    def default_summary(self):
        if self.summary is None:
            self.summary = SummaryPolicyIn(auto=self.kind in {"blog", "post"})
        return self


class SourceRuntimeOut(BaseModel):
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    failure_count: int = 0
    empty_count: int = 0
    last_error: str = ""


class SourceSubscriptionOut(BaseModel):
    source_id: str
    subscribed: bool
    priority_override: int | None = None
    settings_override: dict[str, Any] = Field(default_factory=dict)


class SourceDefinitionOut(SourceDefinitionIn):
    subscribed: bool = False
    runtime: SourceRuntimeOut | None = None
    latest_run: dict[str, Any] | None = None
    content_audit: dict[str, Any] = Field(default_factory=dict)
    spec_hash: str = ""
    catalog_file: str = ""
    # Compatibility fields for existing UI surfaces while the frontend moves to
    # catalog terminology.
    name: str = ""
    content_type: Literal["paper", "blog", "post"] = "blog"
    homepage_url: str = ""
    enabled: bool = False
    is_builtin: bool = True
    language_hint: str = "auto"
    default_tags: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    attempts: list[SourceAttemptIn] = Field(default_factory=list)
    auto_summary_enabled: bool = False
    auto_summary_days: int = 7
    auth_mode: str = "none"
    stability_level: str = "stable"


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
    attempt: FetchAttemptIn | None = None
    source: SourceDefinitionIn | None = None


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


class ItemSourceOut(BaseModel):
    source_id: str
    source_name: str
    url: str = ""
    tags: list[str] = Field(default_factory=list)


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
    sources: list[ItemSourceOut] = Field(default_factory=list)


class ItemListOut(BaseModel):
    items: list[ItemOut]
    total: int


class LLMProviderIn(BaseModel):
    id: int | None = None
    name: str = "Custom API"
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = ""
    api_key: str | None = None
    model_name: str = ""
    temperature: float = 0.2
    timeout: int = Field(default=60, ge=1, le=300)
    enabled: bool = False
    priority: int = 0


class LLMProviderOut(BaseModel):
    id: int
    name: str
    provider_type: str
    base_url: str
    model_name: str
    temperature: float
    timeout: int
    enabled: bool
    priority: int
    has_api_key: bool
    last_error: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    llm_providers: list[LLMProviderOut] = Field(default_factory=list)
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
    llm_providers: list[LLMProviderIn] | None = None


class AiProviderTestResult(BaseModel):
    ok: bool
    provider: str
    model: str | None = None
    duration_ms: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
