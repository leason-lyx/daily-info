export type Source = {
  id: string;
  title: string;
  kind: "paper" | "blog" | "post";
  platform: string;
  homepage: string;
  language: string;
  tags: string[];
  subscribed: boolean;
  effective_priority: number;
  priority_tier: string;
  group: string;
  priority: number;
  fetch: SourceFetch;
  summary: SourceSummary;
  tagging: SourceTagging;
  filters: {
    include_keywords: string[];
    exclude_keywords: string[];
  };
  auth: Record<string, unknown>;
  stability: string;
  runtime?: SourceRuntime | null;
  active_job?: ActiveJob | null;
  latest_item_published_at?: string | null;
  latest_item_ingested_at?: string | null;
  latest_item_title?: string;
  spec_hash?: string;
  catalog_file?: string;
  fulltext: Record<string, unknown>;
  content_audit?: Record<string, unknown>;
  latest_run?: LatestRun | null;
};

export type ActiveJob = {
  id: number;
  type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  scheduled_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error_code?: string;
  error_message?: string;
};

export type SourceFetchAttempt = {
  adapter: "feed" | "rsshub" | "html_index" | "page_index";
  url?: string;
  route?: string;
  timeout_seconds?: number;
  selectors?: string[];
  limit?: number;
  reader_fallback?: boolean;
};

export type SourceFetch = {
  strategy: "first_success";
  interval_seconds: number;
  attempts: SourceFetchAttempt[];
};

export type SourceSummary = {
  auto: boolean;
  window_days: number;
};

export type SourceTagging = {
  mode: "feed" | "llm" | "default";
  max_tags: number;
};

export type SourceRuntime = {
  last_run_at?: string | null;
  last_success_at?: string | null;
  failure_count: number;
  empty_count: number;
  last_error: string;
};

export type SourceDefinitionInput = {
  id: string;
  title: string;
  kind: "paper" | "blog" | "post";
  platform: string;
  homepage: string;
  language: string;
  tags: string[];
  group: string;
  priority?: number;
  fetch: SourceFetch;
  fulltext: Record<string, unknown>;
  summary: SourceSummary;
  tagging?: SourceTagging;
  filters?: { include_keywords?: string[]; exclude_keywords?: string[] };
  auth?: Record<string, unknown>;
  stability?: string;
};

export type SourceDefinitionPatchInput = {
  language?: string;
  tags?: string[];
  group?: string;
  priority?: number;
  fetch?: { interval_seconds?: number };
  fulltext?: {
    mode: "feed_only" | "detail_only" | "feed_then_detail";
    min_feed_chars: number;
    max_detail_pages_per_run: number;
    selectors?: string[];
    remove_selectors?: string[];
    min_detail_chars?: number;
  };
  summary?: SourceSummary;
  tagging?: SourceTagging;
  filters?: { include_keywords?: string[]; exclude_keywords?: string[] };
};

export type LatestRun = {
  id: number;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  raw_count: number;
  item_count: number;
  fulltext_success_count: number;
  summary_queued_count?: number;
  error_code?: string;
  error_message?: string;
};

export type HealthSource = {
  id: string;
  name: string;
  enabled: boolean;
  auto_summary_enabled?: boolean;
  auto_summary_days?: number;
  content_audit?: Record<string, unknown>;
  latest_success_at?: string | null;
  raw_count?: number;
  item_count?: number;
  fulltext_success_count?: number;
  fulltext_success_rate?: number | null;
  summary_ready_count?: number;
  summary_failed_count?: number;
  summary_failure_rate?: number | null;
  latest_run?: LatestRun | null;
  consecutive_failures?: number;
  consecutive_empty?: number;
};

export type HealthJobStatus = "queued" | "running" | "retrying" | "failed" | "succeeded" | "skipped";

export type HealthJobTarget = {
  kind: "source" | "item" | "payload";
  id: string;
  label: string;
};

export type HealthJob = {
  id: number;
  type: string;
  status: HealthJobStatus | string;
  attempts: number;
  max_attempts: number;
  scheduled_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error_code?: string;
  error_message?: string;
  target: HealthJobTarget;
};

export type HealthJobs = {
  counts: Record<HealthJobStatus, number>;
  active: HealthJob[];
  recent: HealthJob[];
};

export type Health = {
  ok?: boolean;
  items_total?: number;
  items_24h?: number;
  jobs?: HealthJobs;
  summary?: Record<string, number>;
  ai_provider?: Record<string, unknown>;
  sources?: HealthSource[];
  degraded_sources?: Array<Record<string, unknown>>;
  recent_errors?: Array<Record<string, unknown>>;
  recent_summary_errors?: Array<Record<string, unknown>>;
};

export type LlmUsageBucket = {
  requests: number;
  success: number;
  failed: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  reasoning_tokens: number;
  duration_ms: number;
};

export type LlmUsage = {
  provider: string;
  all_time: LlmUsageBucket;
  recent_24h: LlmUsageBucket;
  recent_7d: LlmUsageBucket;
  by_model: Array<LlmUsageBucket & { model: string }>;
  last_used_at?: string | null;
  last_error_at?: string | null;
  last_error?: string;
};

export type LlmProvider = {
  id: number;
  name: string;
  provider_type: string;
  base_url: string;
  model_name: string;
  temperature: number;
  timeout: number;
  enabled: boolean;
  priority: number;
  has_api_key: boolean;
  last_error?: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type AiProviderTestResult = {
  ok: boolean;
  provider: string;
  model?: string | null;
  duration_ms: number;
  usage?: Record<string, unknown>;
  error?: string;
};

export type ItemSource = {
  source_id: string;
  source_name: string;
  url: string;
  tags: string[];
};

export type Item = {
  id: string;
  source_id: string;
  source_name: string;
  content_type: "paper" | "blog" | "post";
  platform: string;
  title: string;
  chinese_title: string;
  url: string;
  authors: string[];
  published_at: string | null;
  summary: string;
  raw_text: string;
  ai_summary?: Record<string, unknown> | null;
  tags: string[];
  entities: string[];
  read: boolean;
  starred: boolean;
  hidden: boolean;
  summary_status: string;
  recommendation_score?: number | null;
  recommendation_reasons?: string[];
  recommendation_components?: Record<string, number>;
  sources: ItemSource[];
};

export type FeedPreset = {
  id: string;
  name: string;
  description: string;
  is_builtin: boolean;
  sort_order: number;
  hidden: boolean;
  filter: Record<string, unknown>;
  rank: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ItemEventType =
  | "open"
  | "read"
  | "unread"
  | "star"
  | "unstar"
  | "hide"
  | "unhide"
  | "more_like_this"
  | "less_like_this"
  | "dismiss";

export type RecommendationProfile = {
  profile_id: string;
  interests: string[];
  excluded_terms: string[];
  source_ids: string[];
  tags: string[];
  entities: string[];
  platforms: string[];
  content_types: string[];
  trend_providers: string[];
  weights: Record<string, number>;
  implicit: Record<string, Record<string, number>>;
  updated_at?: string | null;
};
