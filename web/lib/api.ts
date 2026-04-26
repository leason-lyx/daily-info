const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

function apiBase() {
  if (configuredApiBase) return configuredApiBase.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export type Source = {
  id: string;
  name: string;
  content_type: "paper" | "blog" | "post";
  platform: string;
  homepage_url: string;
  enabled: boolean;
  is_builtin: boolean;
  group: string;
  priority: number;
  poll_interval: number;
  auto_summary_enabled: boolean;
  auto_summary_days: number;
  language_hint: string;
  include_keywords: string[];
  exclude_keywords: string[];
  default_tags: string[];
  attempts: SourceAttempt[];
  fulltext: Record<string, unknown>;
  content_audit?: Record<string, unknown>;
  auth_mode: string;
  stability_level: string;
  latest_run?: LatestRun | null;
};

export type SourceAttempt = {
  id?: number;
  kind: string;
  adapter: string;
  url: string;
  route: string;
  priority: number;
  enabled: boolean;
  config: Record<string, unknown>;
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

export type AiProviderTestResult = {
  ok: boolean;
  provider: string;
  model?: string | null;
  duration_ms: number;
  usage?: Record<string, unknown>;
  error?: string;
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
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(readableError(text, res.statusText));
  }
  return res.json() as Promise<T>;
}

function readableError(text: string, fallback: string) {
  if (!text) return fallback;
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed.detail === "string") return parsed.detail;
    if (parsed.detail?.message) return parsed.detail.message;
  } catch {
    // Plain text response.
  }
  return text;
}

export const api = {
  getItems: (query: URLSearchParams) => request<{ items: Item[]; total: number }>(`/api/items?${query.toString()}`),
  getItem: (id: string) => request<Item>(`/api/items/${id}`),
  getSources: () => request<Source[]>("/api/sources"),
  patchSource: (id: string, body: Partial<Source>) => request<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  createSource: (body: Source) => request<Source>("/api/sources", { method: "POST", body: JSON.stringify(body) }),
  fetchSource: (id: string) => request<{ job_id: number; status: string }>(`/api/sources/${id}/fetch`, { method: "POST" }),
  previewSource: (body: Record<string, unknown>) => request<Record<string, unknown>>("/api/sources/preview", { method: "POST", body: JSON.stringify(body) }),
  markItem: (id: string, action: "read" | "star" | "hide") => request<Item>(`/api/items/${id}/${action}`, { method: "POST", body: JSON.stringify({}) }),
  resummarize: (id: string) => request<Item>(`/api/items/${id}/resummarize`, { method: "POST" }),
  health: () => request<Health>("/api/health"),
  settings: () => request<Record<string, unknown>>("/api/settings"),
  patchSettings: (body: Record<string, unknown>) => request<Record<string, unknown>>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),
  testAiProvider: (body: Record<string, unknown>) => request<AiProviderTestResult>("/api/settings/test-ai", { method: "POST", body: JSON.stringify(body) }),
  importSources: async (text: string) => {
    const res = await fetch(`${apiBase()}/api/sources/import`, {
      method: "POST",
      headers: { "Content-Type": "text/yaml" },
      body: text,
    });
    if (!res.ok) throw new Error(readableError(await res.text(), res.statusText));
    return res.json() as Promise<{ imported: number; summary_queued?: number }>;
  },
  exportSources: async () => {
    const res = await fetch(`${apiBase()}/api/sources/export`);
    if (!res.ok) throw new Error(readableError(await res.text(), res.statusText));
    return res.text();
  },
};
