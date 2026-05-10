const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

function isLoopbackHost(hostname: string) {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname === "[::1]";
}

export function apiBase() {
  if (configuredApiBase) {
    const trimmed = configuredApiBase.replace(/\/$/, "");
    if (typeof window !== "undefined" && !isLoopbackHost(window.location.hostname)) {
      try {
        const parsed = new URL(trimmed);
        if (isLoopbackHost(parsed.hostname)) {
          parsed.protocol = window.location.protocol;
          parsed.hostname = window.location.hostname;
          return parsed.toString().replace(/\/$/, "");
        }
      } catch {
        return trimmed;
      }
    }
    return trimmed;
  }
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(readableError(text, res.statusText));
  }
  return res.json() as Promise<T>;
}

export function readableError(text: string, fallback: string) {
  if (!text) return fallback;
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed.detail === "string") return parsed.detail;
    if (parsed.detail?.message) return parsed.detail.message;
  } catch {
    // Plain text response.
  }
  return text;
}

