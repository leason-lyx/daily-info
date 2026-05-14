import { expect, test } from "@playwright/test";

async function expectNoNextError(page: import("@playwright/test").Page) {
  await expect(page.locator("nextjs-portal")).toHaveCount(0);
  await expect(page.getByText(/Application error|Unhandled Runtime Error|Build Error/i)).toHaveCount(0);
}

test.describe("Compose-served UI smoke", () => {
  test("core pages render meaningful content", async ({ page }) => {
    for (const route of ["/", "/sources", "/health", "/settings"]) {
      await page.goto(route);
      await expect(page.locator("body")).not.toBeEmpty();
      await expectNoNextError(page);
    }
  });

  test("sources search, facet, and refresh controls respond", async ({ page }) => {
    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Source Catalog" })).toBeVisible();
    await page.getByLabel("Search").fill("arxiv");
    await page.getByLabel("Kind").selectOption("paper").catch(() => undefined);
    await page.getByRole("button", { name: /Refresh|Refreshing/ }).click();
    await expectNoNextError(page);
  });

  test("health refresh and expandable sections respond", async ({ page }) => {
    await page.goto("/health");
    await expect(page.getByRole("heading", { name: "Health" })).toBeVisible();
    await page.getByRole("button", { name: "Refresh health" }).click();
    const showAll = page.getByRole("button", { name: /Show all/ }).first();
    if (await showAll.isVisible().catch(() => false)) {
      const section = showAll.locator("xpath=ancestor::section[1]");
      await showAll.click();
      await expect(section.getByRole("button", { name: /Show less/ })).toHaveAttribute("aria-expanded", "true");
    }
    await expectNoNextError(page);
  });

  test("settings recommendation fields are editable without external AI calls", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    const interests = page.getByLabel("兴趣词");
    await interests.fill("agent evaluation");
    await expect(interests).toHaveValue("agent evaluation");
    await page.getByLabel("摘要 AI").selectOption("none");
    await expect(page.getByText("摘要 AI 已关闭，无需测试。")).toBeVisible();
    await expectNoNextError(page);
  });
});
