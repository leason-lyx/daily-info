import { apiBase, readableError, request } from "@/lib/apiClient";
import type {
  AiProviderTestResult,
  FeedPreset,
  Health,
  Item,
  ItemEventType,
  RecommendationProfile,
  Source,
  SourceDefinitionInput,
  SourceDefinitionPatchInput,
} from "@/lib/api";

export const feedApi = {
  getItems: (query: URLSearchParams) => request<{ items: Item[]; total: number }>(`/api/items?${query.toString()}`),
  getFeedPresets: () => request<FeedPreset[]>("/api/feed-presets"),
  createFeedPreset: (body: Record<string, unknown>) => request<FeedPreset>("/api/feed-presets", { method: "POST", body: JSON.stringify(body) }),
  patchFeedPreset: (id: string, body: Record<string, unknown>) => request<FeedPreset>(`/api/feed-presets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteFeedPreset: (id: string) => request<{ deleted: string }>(`/api/feed-presets/${id}`, { method: "DELETE" }),
};

export const itemsApi = {
  getItem: (id: string) => request<Item>(`/api/items/${id}`),
  markItem: (id: string, action: "read" | "star") => request<Item>(`/api/items/${id}/${action}`, { method: "POST", body: JSON.stringify({}) }),
  recordItemEvent: (id: string, event_type: ItemEventType, metadata: Record<string, unknown> = {}) =>
    request<Record<string, unknown>>(`/api/items/${id}/events`, { method: "POST", body: JSON.stringify({ event_type, metadata }) }),
  resummarize: (id: string) => request<Item>(`/api/items/${id}/resummarize`, { method: "POST" }),
};

export const sourcesApi = {
  getSources: () => request<Source[]>("/api/source-definitions"),
  getSubscriptions: () => request<Array<{ source_id: string; subscribed: boolean }>>("/api/subscriptions"),
  subscribeSource: (id: string) => request<{ source_id: string; subscribed: boolean }>(`/api/subscriptions/${id}`, { method: "POST" }),
  unsubscribeSource: (id: string) => request<{ source_id: string; subscribed: boolean }>(`/api/subscriptions/${id}`, { method: "DELETE" }),
  patchSource: (id: string, body: Partial<Source>) => request<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  patchSourceDefinition: (id: string, body: SourceDefinitionPatchInput) => request<Source>(`/api/source-definitions/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  createSource: (body: SourceDefinitionInput) => request<Source>("/api/source-definitions", { method: "POST", body: JSON.stringify(body) }),
  fetchSource: (id: string) => request<{ job_id: number; status: string }>(`/api/sources/${id}/fetch`, { method: "POST" }),
  previewSource: (body: Record<string, unknown>) => request<Record<string, unknown>>("/api/sources/preview", { method: "POST", body: JSON.stringify(body) }),
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

export const settingsApi = {
  getRecommendationProfile: () => request<RecommendationProfile>("/api/recommendation/profile"),
  patchRecommendationProfile: (body: Partial<RecommendationProfile>) =>
    request<RecommendationProfile>("/api/recommendation/profile", { method: "PATCH", body: JSON.stringify(body) }),
  settings: () => request<Record<string, unknown>>("/api/settings"),
  patchSettings: (body: Record<string, unknown>) => request<Record<string, unknown>>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),
  testAiProvider: (body: Record<string, unknown>) => request<AiProviderTestResult>("/api/settings/test-ai", { method: "POST", body: JSON.stringify(body) }),
};

export const healthApi = {
  health: () => request<Health>("/api/health"),
};

export const api = {
  ...feedApi,
  ...itemsApi,
  ...sourcesApi,
  ...settingsApi,
  ...healthApi,
};

