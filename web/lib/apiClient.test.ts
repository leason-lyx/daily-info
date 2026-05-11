import { afterEach, describe, expect, it, vi } from "vitest";

async function loadApiClient(apiBase?: string) {
  vi.resetModules();
  if (apiBase === undefined) {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
  } else {
    process.env.NEXT_PUBLIC_API_BASE_URL = apiBase;
  }
  return import("./apiClient");
}

function setWindowUrl(url: string) {
  const jsdom = (globalThis as unknown as { jsdom?: { reconfigure: (options: { url: string }) => void } }).jsdom;
  if (jsdom) {
    jsdom.reconfigure({ url });
    return;
  }
  window.history.replaceState({}, "", url);
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.NEXT_PUBLIC_API_BASE_URL;
  setWindowUrl("http://localhost/");
});

describe("apiClient", () => {
  it("uses the browser host when no API base is configured", async () => {
    setWindowUrl("https://daily.tail.ts.net/settings");
    const { apiBase } = await loadApiClient();

    expect(apiBase()).toBe("https://daily.tail.ts.net:8000");
  });

  it("trims configured API base URLs", async () => {
    const { apiBase } = await loadApiClient("https://api.example.com/");

    expect(apiBase()).toBe("https://api.example.com");
  });

  it("rewrites configured localhost API hosts for non-localhost pages", async () => {
    setWindowUrl("https://daily.tail.ts.net/sources");
    const { apiBase } = await loadApiClient("http://localhost:8000/");

    expect(apiBase()).toBe("https://daily.tail.ts.net:8000");
  });

  it("keeps malformed configured API bases as-is", async () => {
    setWindowUrl("https://daily.tail.ts.net/");
    const { apiBase } = await loadApiClient("not a url/");

    expect(apiBase()).toBe("not a url");
  });

  it("parses successful JSON responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { request } = await loadApiClient("https://api.example.com");

    await expect(request("/api/health")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith("https://api.example.com/api/health", {
      headers: { "Content-Type": "application/json" },
    });
  });

  it("throws readable errors for failed requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { message: "Bad source" } }), { status: 400, statusText: "Bad Request" })),
    );
    const { request } = await loadApiClient("https://api.example.com");

    await expect(request("/api/sources/preview")).rejects.toThrow("Bad source");
  });

  it("falls back to status text or plain text error bodies", async () => {
    const { readableError } = await loadApiClient();

    expect(readableError("", "Bad Gateway")).toBe("Bad Gateway");
    expect(readableError("plain failure", "Bad Gateway")).toBe("plain failure");
    expect(readableError(JSON.stringify({ detail: "Detailed failure" }), "Bad Gateway")).toBe("Detailed failure");
  });
});
