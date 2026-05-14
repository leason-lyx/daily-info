import { render, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "./page";
import { recommendationProfile } from "@/test/factories";

const api = vi.hoisted(() => ({
  settingsApi: {
    settings: vi.fn(),
    patchSettings: vi.fn(),
    getRecommendationProfile: vi.fn(),
    patchRecommendationProfile: vi.fn(),
    testAiProvider: vi.fn(),
  },
}));

vi.mock("@/lib/apiDomains", () => api);

const settings = {
  database_url: "sqlite:////data/daily-info.db",
  llm_provider_type: "openai_compatible",
  llm_configured: true,
  llm_model_name: "gpt-test",
  llm_providers: [
    {
      id: 1,
      name: "Primary API",
      provider_type: "openai_compatible",
      base_url: "https://api.primary.example/v1",
      model_name: "primary-model",
      temperature: 0.2,
      timeout: 60,
      enabled: true,
      priority: 0,
      has_api_key: true,
    },
    {
      id: 2,
      name: "Backup API",
      provider_type: "openai_compatible",
      base_url: "https://api.backup.example/v1",
      model_name: "backup-model",
      temperature: 0.4,
      timeout: 90,
      enabled: false,
      priority: 1,
      has_api_key: false,
    },
  ],
  llm_usage: {
    provider: "openai_compatible",
    all_time: { requests: 10, success: 9, failed: 1, prompt_tokens: 100, completion_tokens: 50, total_tokens: 150, reasoning_tokens: 0, duration_ms: 1000 },
    recent_24h: { requests: 2, success: 2, failed: 0, prompt_tokens: 20, completion_tokens: 10, total_tokens: 30, reasoning_tokens: 0, duration_ms: 100 },
    recent_7d: { requests: 4, success: 4, failed: 0, prompt_tokens: 40, completion_tokens: 20, total_tokens: 60, reasoning_tokens: 0, duration_ms: 200 },
    by_model: [],
  },
};

describe("SettingsPage", () => {
  beforeEach(() => {
    api.settingsApi.settings.mockResolvedValue(settings);
    api.settingsApi.getRecommendationProfile.mockResolvedValue(recommendationProfile());
    api.settingsApi.patchRecommendationProfile.mockImplementation(async (body) => recommendationProfile(body));
    api.settingsApi.patchSettings.mockResolvedValue({ ok: true });
    api.settingsApi.testAiProvider.mockResolvedValue({ ok: true, provider: "openai_compatible", model: "primary-model", duration_ms: 12 });
  });

  it("loads runtime settings and saves recommendation preferences", async () => {
    const user = userEvent.setup();
    render(createElement(SettingsPage));

    expect(await screen.findByText("SQLite · /data/daily-info.db")).toBeInTheDocument();
    expect(screen.getByText("自定义 API · primary-model")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("兴趣词"));
    await user.type(screen.getByLabelText("兴趣词"), "agents\nbenchmarks\nagents");
    await user.clear(screen.getByLabelText("排除词"));
    await user.type(screen.getByLabelText("排除词"), "ads");
    await user.click(screen.getByRole("button", { name: /保存推荐偏好/ }));

    await waitFor(() => expect(api.settingsApi.patchRecommendationProfile).toHaveBeenCalled());
    expect(api.settingsApi.patchRecommendationProfile.mock.calls[0][0]).toMatchObject({
      interests: ["agents", "benchmarks"],
      excluded_terms: ["ads"],
    });
    expect(await screen.findByText("推荐偏好已保存。")).toBeInTheDocument();
  });

  it("switches providers, edits custom providers, tests a provider, and saves settings", async () => {
    const user = userEvent.setup();
    render(createElement(SettingsPage));
    await screen.findByDisplayValue("Primary API");

    await user.selectOptions(screen.getByLabelText("摘要 AI"), "none");
    expect(screen.getByText("摘要 AI 已关闭，无需测试。")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("摘要 AI"), "openai_compatible");
    await user.click(screen.getByRole("button", { name: /Add API/ }));
    expect(screen.getByDisplayValue("Custom API 3")).toBeInTheDocument();

    const primaryRow = screen.getByDisplayValue("Primary API").closest(".providerRow");
    expect(primaryRow).not.toBeNull();
    await user.click(within(primaryRow as HTMLElement).getByRole("button", { name: "Test API" }));
    expect(await screen.findByText("自定义 API test succeeded in 12ms.")).toBeInTheDocument();

    const backupRow = screen.getByDisplayValue("Backup API").closest(".providerRow");
    expect(backupRow).not.toBeNull();
    await user.click(within(backupRow as HTMLElement).getByRole("button", { name: "Move up" }));

    const newRow = screen.getByDisplayValue("Custom API 3").closest(".providerRow");
    expect(newRow).not.toBeNull();
    await user.click(within(newRow as HTMLElement).getByRole("button", { name: "Delete API" }));
    expect(screen.queryByDisplayValue("Custom API 3")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(api.settingsApi.patchSettings).toHaveBeenCalled());
    expect(api.settingsApi.patchSettings.mock.calls[0][0]).toMatchObject({
      llm_provider_type: "openai_compatible",
      llm_providers: [
        expect.objectContaining({ name: "Backup API", priority: 0 }),
        expect.objectContaining({ name: "Primary API", priority: 1 }),
      ],
    });
    expect(await screen.findByText("设置已保存。")).toBeInTheDocument();
  });

  it("tests and saves the Codex provider path", async () => {
    const user = userEvent.setup();
    api.settingsApi.testAiProvider.mockResolvedValueOnce({ ok: false, provider: "codex_cli", duration_ms: 5, error: "codex unavailable" });
    render(createElement(SettingsPage));
    await screen.findByDisplayValue("Primary API");

    await user.selectOptions(screen.getByLabelText("摘要 AI"), "codex_cli");
    await user.type(screen.getByLabelText("Codex model"), "gpt-test");
    await user.click(screen.getByRole("button", { name: /Test Codex/ }));
    expect(await screen.findByText("codex unavailable")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(api.settingsApi.patchSettings).toHaveBeenCalled());
    expect(api.settingsApi.patchSettings.mock.calls[0][0]).toMatchObject({
      llm_provider_type: "codex_cli",
      codex_cli_path: "codex",
      codex_cli_model: "gpt-test",
    });
  });

  it("shows settings errors", async () => {
    api.settingsApi.settings.mockRejectedValueOnce(new Error("settings down"));
    render(createElement(SettingsPage));

    expect(await screen.findByText("settings down")).toBeInTheDocument();
  });
});
