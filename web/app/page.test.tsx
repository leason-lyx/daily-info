import { render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import FeedPage from "./page";
import { feedPreset, item, source } from "@/test/factories";

const api = vi.hoisted(() => ({
  feedApi: {
    getItems: vi.fn(),
    getFeedPresets: vi.fn(),
    createFeedPreset: vi.fn(),
    deleteFeedPreset: vi.fn(),
  },
  itemsApi: {
    getItem: vi.fn(),
    markItem: vi.fn(),
    recordItemEvent: vi.fn(),
    resummarize: vi.fn(),
  },
  sourcesApi: {
    getSources: vi.fn(),
  },
}));

vi.mock("@/lib/apiDomains", () => api);

describe("FeedPage", () => {
  beforeEach(() => {
    api.sourcesApi.getSources.mockResolvedValue([
      source(),
      source({ id: "engineering-blog", title: "Engineering Blog", kind: "blog", group: "Engineering Blogs", subscribed: true, priority: 90, effective_priority: 90, priority_tier: "p2" }),
      source({ id: "unsubscribed", title: "Unsubscribed", subscribed: false }),
    ]);
    api.feedApi.getFeedPresets.mockResolvedValue([
      feedPreset(),
      feedPreset({ id: "for-you", name: "For You", description: "Recommended", rank: { mode: "for_you" }, sort_order: 1 }),
    ]);
    api.feedApi.getItems.mockResolvedValue({ items: [item()], total: 1 });
    api.feedApi.createFeedPreset.mockResolvedValue(feedPreset({ id: "custom", name: "My View", is_builtin: false, sort_order: 10 }));
    api.feedApi.deleteFeedPreset.mockResolvedValue({ deleted: "custom" });
    api.itemsApi.markItem.mockResolvedValue(item({ read: true }));
    api.itemsApi.resummarize.mockResolvedValue(item({ summary_status: "not_configured" }));
    api.itemsApi.recordItemEvent.mockResolvedValue({ ok: true });
    api.itemsApi.getItem.mockResolvedValue(item());
  });

  it("loads feed data and builds the API query from URL filters", async () => {
    window.history.replaceState({}, "", "/?q=agent&rank=for_you");
    render(createElement(FeedPage));

    expect(await screen.findByRole("heading", { name: "智能体软件工程评测" })).toBeInTheDocument();
    expect(api.feedApi.getItems).toHaveBeenCalledTimes(1);
    const query = api.feedApi.getItems.mock.calls[0][0] as URLSearchParams;
    expect(query.get("q")).toBe("agent");
    expect(query.get("rank")).toBe("for_you");
    expect(query.getAll("source_id")).toEqual([]);
    expect(screen.getByText("这是一篇关于软件工程智能体评测的论文。")).toBeInTheDocument();
  });

  it("updates URL filters and saves the current view as a preset", async () => {
    const user = userEvent.setup();
    render(createElement(FeedPage));
    await screen.findByRole("heading", { name: "智能体软件工程评测" });

    await user.selectOptions(screen.getByLabelText("Priority"), "p0");
    expect(window.location.search).toContain("priority_tier=p0");

    await user.type(screen.getByLabelText("自定义预设名称"), "My View");
    await user.click(screen.getByRole("button", { name: /保存视图/ }));

    await waitFor(() => expect(api.feedApi.createFeedPreset).toHaveBeenCalled());
    expect(api.feedApi.createFeedPreset.mock.calls[0][0]).toMatchObject({
      name: "My View",
      rank: { mode: "latest" },
    });
    expect(await screen.findByText("已保存 My View")).toBeInTheDocument();
  });

  it("handles item read, summary, and two-way recommendation feedback actions", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?rank=for_you");
    render(createElement(FeedPage));
    await screen.findByRole("heading", { name: "智能体软件工程评测" });

    await user.click(screen.getByRole("button", { name: "标为已读" }));
    await waitFor(() => expect(api.itemsApi.markItem).toHaveBeenCalledWith("item-1", "read"));
    expect(await screen.findByText("已读")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Star item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "不感兴趣" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Original" }));
    expect(api.itemsApi.recordItemEvent).toHaveBeenCalledWith("item-1", "open", { url: "https://example.com/item" });

    await user.click(screen.getByRole("button", { name: "重新生成摘要" }));
    expect(api.itemsApi.resummarize).toHaveBeenCalledWith("item-1");

    const moreButton = screen.getByRole("button", { name: "我喜欢更多这样的内容" });
    const lessButton = screen.getByRole("button", { name: "我不喜欢推荐这样的内容" });
    expect(moreButton).toHaveAttribute("aria-pressed", "false");
    expect(lessButton).toHaveAttribute("aria-pressed", "false");

    await user.click(moreButton);
    await waitFor(() => expect(api.itemsApi.recordItemEvent).toHaveBeenCalledWith("item-1", "more_like_this", { preset_id: "all", rank: "for_you" }));
    expect(await screen.findByText("已记录：喜欢更多这样的内容")).toBeInTheDocument();
    expect(moreButton).toHaveAttribute("aria-pressed", "true");
    expect(lessButton).toHaveAttribute("aria-pressed", "false");

    await user.click(lessButton);
    await waitFor(() => expect(api.itemsApi.recordItemEvent).toHaveBeenCalledWith("item-1", "less_like_this", { preset_id: "all", rank: "for_you" }));
    expect(await screen.findByText("已记录：不喜欢推荐这样的内容")).toBeInTheDocument();
    expect(moreButton).toHaveAttribute("aria-pressed", "false");
    expect(lessButton).toHaveAttribute("aria-pressed", "true");

    expect(screen.queryByRole("button", { name: "不感兴趣" })).not.toBeInTheDocument();
  });

  it("shows API errors for the active query", async () => {
    api.feedApi.getItems.mockRejectedValueOnce(new Error("Feed unavailable"));

    render(createElement(FeedPage));

    expect(await screen.findByText("Feed unavailable")).toBeInTheDocument();
  });

  it("shows loading and empty states", async () => {
    api.sourcesApi.getSources.mockResolvedValueOnce([source()]);
    api.feedApi.getItems.mockResolvedValueOnce({ items: [], total: 0 });

    render(createElement(FeedPage));

    expect(await screen.findByText("No items yet. Subscribe to a source and run fetch.")).toBeInTheDocument();
  });
});
