import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

const navigation = vi.hoisted(() => {
  function setUrl(href: string) {
    const url = href.startsWith("http") ? href : `${window.location.origin}${href}`;
    window.history.pushState({}, "", url);
  }

  return {
    router: {
      back: vi.fn(),
      forward: vi.fn(),
      prefetch: vi.fn(),
      refresh: vi.fn(),
      replace: vi.fn((href: string) => setUrl(href)),
      push: vi.fn((href: string) => setUrl(href)),
    },
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => window.location.pathname,
  useRouter: () => navigation.router,
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState({}, "", "http://localhost/");
});
