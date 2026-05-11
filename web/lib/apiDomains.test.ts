import { beforeEach, describe, expect, it, vi } from "vitest";
import { feedApi, healthApi, itemsApi, settingsApi, sourcesApi } from "@/lib/apiDomains";
import { request } from "@/lib/apiClient";

vi.mock("@/lib/apiClient", () => ({
  request: vi.fn().mockResolvedValue({ ok: true }),
}));

const requestMock = vi.mocked(request);

describe("apiDomains", () => {
  beforeEach(() => {
    requestMock.mockClear();
  });

  it("builds feed endpoints and serializes preset bodies", async () => {
    const query = new URLSearchParams({ q: "agents" });

    await feedApi.getItems(query);
    await feedApi.createFeedPreset({ name: "Mine" });
    await feedApi.deleteFeedPreset("custom");

    expect(requestMock).toHaveBeenNthCalledWith(1, "/api/items?q=agents");
    expect(requestMock).toHaveBeenNthCalledWith(2, "/api/feed-presets", { method: "POST", body: JSON.stringify({ name: "Mine" }) });
    expect(requestMock).toHaveBeenNthCalledWith(3, "/api/feed-presets/custom", { method: "DELETE" });
  });

  it("builds item action endpoints", async () => {
    await itemsApi.getItem("item-1");
    await itemsApi.markItem("item-1", "read");
    await itemsApi.recordItemEvent("item-1", "more_like_this", { preset_id: "for-you" });
    await itemsApi.resummarize("item-1");

    expect(requestMock).toHaveBeenNthCalledWith(1, "/api/items/item-1");
    expect(requestMock).toHaveBeenNthCalledWith(2, "/api/items/item-1/read", { method: "POST", body: JSON.stringify({}) });
    expect(requestMock).toHaveBeenNthCalledWith(3, "/api/items/item-1/events", {
      method: "POST",
      body: JSON.stringify({ event_type: "more_like_this", metadata: { preset_id: "for-you" } }),
    });
    expect(requestMock).toHaveBeenNthCalledWith(4, "/api/items/item-1/resummarize", { method: "POST" });
  });

  it("builds source endpoints", async () => {
    await sourcesApi.getSources();
    await sourcesApi.subscribeSource("source-1");
    await sourcesApi.unsubscribeSource("source-1");
    await sourcesApi.patchSourceDefinition("source-1", { priority: 10 });
    await sourcesApi.fetchSource("source-1");
    await sourcesApi.previewSource({ url: "https://example.com/feed.xml" });

    expect(requestMock).toHaveBeenNthCalledWith(1, "/api/source-definitions");
    expect(requestMock).toHaveBeenNthCalledWith(2, "/api/subscriptions/source-1", { method: "POST" });
    expect(requestMock).toHaveBeenNthCalledWith(3, "/api/subscriptions/source-1", { method: "DELETE" });
    expect(requestMock).toHaveBeenNthCalledWith(4, "/api/source-definitions/source-1", { method: "PATCH", body: JSON.stringify({ priority: 10 }) });
    expect(requestMock).toHaveBeenNthCalledWith(5, "/api/sources/source-1/fetch", { method: "POST" });
    expect(requestMock).toHaveBeenNthCalledWith(6, "/api/sources/preview", { method: "POST", body: JSON.stringify({ url: "https://example.com/feed.xml" }) });
  });

  it("builds settings and health endpoints", async () => {
    await settingsApi.getRecommendationProfile();
    await settingsApi.patchRecommendationProfile({ interests: ["agents"] });
    await settingsApi.settings();
    await settingsApi.patchSettings({ llm_provider_type: "none" });
    await settingsApi.testAiProvider({ llm_provider_type: "codex_cli" });
    await healthApi.health();

    expect(requestMock).toHaveBeenNthCalledWith(1, "/api/recommendation/profile");
    expect(requestMock).toHaveBeenNthCalledWith(2, "/api/recommendation/profile", { method: "PATCH", body: JSON.stringify({ interests: ["agents"] }) });
    expect(requestMock).toHaveBeenNthCalledWith(3, "/api/settings");
    expect(requestMock).toHaveBeenNthCalledWith(4, "/api/settings", { method: "PATCH", body: JSON.stringify({ llm_provider_type: "none" }) });
    expect(requestMock).toHaveBeenNthCalledWith(5, "/api/settings/test-ai", { method: "POST", body: JSON.stringify({ llm_provider_type: "codex_cli" }) });
    expect(requestMock).toHaveBeenNthCalledWith(6, "/api/health");
  });
});
