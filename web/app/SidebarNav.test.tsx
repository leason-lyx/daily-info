import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { SidebarNav } from "./SidebarNav";

describe("SidebarNav", () => {
  it("marks the current top-level route as active", () => {
    window.history.replaceState({}, "", "/sources/new");

    render(createElement(SidebarNav));

    expect(screen.getByRole("link", { name: /Sources/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Feed/ })).not.toHaveAttribute("aria-current");
  });

  it("only marks Feed active on the root path", () => {
    window.history.replaceState({}, "", "/settings");

    render(createElement(SidebarNav));

    expect(screen.getByRole("link", { name: /Settings/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Feed/ })).not.toHaveAttribute("aria-current");
  });
});
