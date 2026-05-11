import { render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NewSourcePage from "./page";
import { source } from "@/test/factories";

const api = vi.hoisted(() => ({
  sourcesApi: {
    previewSource: vi.fn(),
    createSource: vi.fn(),
  },
}));

vi.mock("@/lib/apiDomains", () => api);

describe("NewSourcePage", () => {
  beforeEach(() => {
    api.sourcesApi.previewSource.mockResolvedValue({ entries: [{ title: "Example Feed" }] });
    api.sourcesApi.createSource.mockResolvedValue(source({ id: "example-feed", title: "Example Feed" }));
  });

  it("previews a source, auto-fills the name, and saves the payload", async () => {
    const user = userEvent.setup();
    render(createElement(NewSourcePage));

    expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled();
    await user.type(screen.getByLabelText("URL"), "https://example.com/feed.xml");
    await user.selectOptions(screen.getByLabelText("Type"), "blog");
    await user.click(screen.getByRole("button", { name: /Preview/ }));

    await waitFor(() => expect(api.sourcesApi.previewSource).toHaveBeenCalledWith({
      url: "https://example.com/feed.xml",
      route: undefined,
      adapter: "feed",
      content_type: "blog",
    }));
    expect(screen.getByLabelText("Name")).toHaveValue("Example Feed");

    await user.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(api.sourcesApi.createSource).toHaveBeenCalled());
    expect(api.sourcesApi.createSource.mock.calls[0][0]).toMatchObject({
      id: "example-feed",
      title: "Example Feed",
      platform: "example.com",
      homepage: "https://example.com/feed.xml",
      kind: "blog",
    });
    expect(await screen.findByText("Saved example-feed")).toBeInTheDocument();
  });

  it("validates that name, URL, or route exists before saving", async () => {
    const user = userEvent.setup();
    api.sourcesApi.previewSource.mockResolvedValueOnce({ entries: [] });
    render(createElement(NewSourcePage));

    await user.click(screen.getByRole("button", { name: /Preview/ }));
    await user.click(screen.getByRole("button", { name: /Save/ }));

    expect(await screen.findByText("Name, URL, or RSSHub route is required.")).toBeInTheDocument();
    expect(api.sourcesApi.createSource).not.toHaveBeenCalled();
  });

  it("shows preview failures", async () => {
    const user = userEvent.setup();
    api.sourcesApi.previewSource.mockRejectedValueOnce(new Error("preview failed"));
    render(createElement(NewSourcePage));

    await user.click(screen.getByRole("button", { name: /Preview/ }));

    expect(await screen.findByText("preview failed")).toBeInTheDocument();
  });

  it("supports RSSHub route sources and save failures", async () => {
    const user = userEvent.setup();
    api.sourcesApi.previewSource.mockResolvedValueOnce({ entries: [{ title: "" }] });
    api.sourcesApi.createSource.mockRejectedValueOnce(new Error("save failed"));
    render(createElement(NewSourcePage));

    await user.type(screen.getByLabelText("RSSHub route"), "/twitter/user/openai");
    await user.selectOptions(screen.getByLabelText("Adapter"), "rsshub");
    await user.selectOptions(screen.getByLabelText("Type"), "post");
    await user.click(screen.getByRole("button", { name: /Preview/ }));
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(api.sourcesApi.createSource).toHaveBeenCalled());
    expect(api.sourcesApi.createSource.mock.calls[0][0]).toMatchObject({
      id: "twitter-user-openai",
      platform: "rsshub",
      group: "Posts",
      summary: { auto: true, window_days: 7 },
      fulltext: { mode: "feed_only" },
    });
    expect(await screen.findByText("save failed")).toBeInTheDocument();
  });
});
