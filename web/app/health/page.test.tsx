import { render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HealthPage from "./page";
import { health } from "@/test/factories";

const api = vi.hoisted(() => ({
  healthApi: {
    health: vi.fn(),
  },
}));

vi.mock("@/lib/apiDomains", () => api);

describe("HealthPage", () => {
  beforeEach(() => {
    api.healthApi.health.mockResolvedValue(health());
  });

  it("shows the loading state before health data resolves", () => {
    api.healthApi.health.mockReturnValue(new Promise(() => undefined));

    render(createElement(HealthPage));

    expect(screen.getByText("Loading health...")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading health overview")).toBeInTheDocument();
  });

  it("renders overview metrics and expands capped sections", async () => {
    const user = userEvent.setup();
    render(createElement(HealthPage));

    expect(await screen.findByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("56 in 24h")).toBeInTheDocument();
    expect(screen.getByText("openai_compatible")).toBeInTheDocument();
    expect(screen.getByText("Bad Source")).toBeInTheDocument();

    const showAllJobs = screen.getByRole("button", { name: "Show all 7" });
    await user.click(showAllJobs);
    expect(showAllJobs).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("7 active")).toBeInTheDocument();

    const showAllSources = screen.getByRole("button", { name: "Show all 8" });
    await user.click(showAllSources);
    expect(showAllSources).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("8 sources")).toBeInTheDocument();
  });

  it("refreshes health data", async () => {
    const user = userEvent.setup();
    render(createElement(HealthPage));
    await screen.findByText("1,234");

    api.healthApi.health.mockResolvedValueOnce(health({ items_total: 2000, items_24h: 5 }));
    await user.click(screen.getByRole("button", { name: "Refresh health" }));

    await waitFor(() => expect(screen.getByText("2,000")).toBeInTheDocument());
    expect(screen.getByText("5 in 24h")).toBeInTheDocument();
  });

  it("shows health loading errors", async () => {
    api.healthApi.health.mockRejectedValueOnce(new Error("health down"));
    render(createElement(HealthPage));

    expect(await screen.findByText("Could not load health status: health down")).toBeInTheDocument();
  });
});
