import { render, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourcesPage from "./page";
import { source } from "@/test/factories";

const api = vi.hoisted(() => ({
  sourcesApi: {
    getSources: vi.fn(),
    subscribeSource: vi.fn(),
    unsubscribeSource: vi.fn(),
    patchSourceDefinition: vi.fn(),
    fetchSource: vi.fn(),
    previewSource: vi.fn(),
  },
}));

vi.mock("@/lib/apiDomains", () => api);

describe("SourcesPage", () => {
  const rows = [
    source(),
    source({ id: "openai-blog", title: "OpenAI Blog", kind: "blog", platform: "openai", group: "Model Labs", priority: 60, effective_priority: 60, priority_tier: "p1" }),
    source({ id: "hn", title: "Hacker News", kind: "post", platform: "hn", group: "AI News", subscribed: false, priority: 150, effective_priority: 150, priority_tier: "p3" }),
  ];

  beforeEach(() => {
    api.sourcesApi.getSources.mockResolvedValue(rows);
    api.sourcesApi.subscribeSource.mockResolvedValue({ source_id: "hn", subscribed: true });
    api.sourcesApi.unsubscribeSource.mockResolvedValue({ source_id: "arxiv-cs-se", subscribed: false });
    api.sourcesApi.fetchSource.mockResolvedValue({ job_id: 42, status: "queued" });
    api.sourcesApi.previewSource.mockResolvedValue({ entries: [{ title: "Preview item" }] });
    api.sourcesApi.patchSourceDefinition.mockResolvedValue(rows[0]);
  });

  it("loads sources and filters by search and facets", async () => {
    const user = userEvent.setup();
    render(createElement(SourcesPage));

    expect(await screen.findByRole("heading", { name: "arXiv CS SE" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search"), "openai");
    expect(screen.queryByRole("heading", { name: "arXiv CS SE" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "OpenAI Blog" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Kind"), "paper");
    expect(screen.getByText("No sources match current filters.")).toBeInTheDocument();
  });

  it("subscribes, previews, fetches, and reports failures", async () => {
    const user = userEvent.setup();
    render(createElement(SourcesPage));
    await screen.findByRole("heading", { name: "arXiv CS SE" });

    await user.click(screen.getByRole("button", { name: "Unsubscribe from arXiv CS SE" }));
    await waitFor(() => expect(api.sourcesApi.unsubscribeSource).toHaveBeenCalledWith("arxiv-cs-se"));
    expect(await screen.findByText(/Unsubscribed arXiv CS SE/)).toBeInTheDocument();

    await user.click(within(screen.getByLabelText("arXiv CS SE actions")).getByRole("button", { name: /Preview/ }));
    expect(await screen.findByText(/Preview item/)).toBeInTheDocument();

    await user.click(within(screen.getByLabelText("arXiv CS SE actions")).getByRole("button", { name: /Fetch now/ }));
    expect(await screen.findByText("Queued arXiv CS SE fetch job 42.")).toBeInTheDocument();

    api.sourcesApi.previewSource.mockRejectedValueOnce(new Error("Preview failed"));
    await user.click(within(screen.getByLabelText("arXiv CS SE actions")).getByRole("button", { name: /Preview/ }));
    expect(await screen.findByText("Could not preview arXiv CS SE: Preview failed")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Subscribe to Hacker News" }));
    await waitFor(() => expect(api.sourcesApi.subscribeSource).toHaveBeenCalledWith("hn"));
    expect(await screen.findByText(/Subscribed Hacker News/)).toBeInTheDocument();
  });

  it("opens the editor and saves source configuration payloads", async () => {
    const user = userEvent.setup();
    render(createElement(SourcesPage));
    await screen.findByRole("heading", { name: "arXiv CS SE" });

    await user.click(within(screen.getByLabelText("arXiv CS SE actions")).getByRole("button", { name: /Configure/ }));
    await user.clear(screen.getByLabelText("Priority"));
    await user.type(screen.getByLabelText("Priority"), "15");
    await user.clear(screen.getByLabelText("Include keywords"));
    await user.type(screen.getByLabelText("Include keywords"), "agent\nbenchmark");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(api.sourcesApi.patchSourceDefinition).toHaveBeenCalled());
    expect(api.sourcesApi.patchSourceDefinition.mock.calls[0][0]).toBe("arxiv-cs-se");
    expect(api.sourcesApi.patchSourceDefinition.mock.calls[0][1]).toMatchObject({
      priority: 15,
      filters: { include_keywords: ["agent", "benchmark"] },
    });
    expect(await screen.findByText("Saved arXiv CS SE to the database catalog.")).toBeInTheDocument();
  });

  it("shows source loading errors", async () => {
    api.sourcesApi.getSources.mockRejectedValueOnce(new Error("catalog down"));
    render(createElement(SourcesPage));

    expect(await screen.findByText("Could not load source catalog: catalog down")).toBeInTheDocument();
  });
});
