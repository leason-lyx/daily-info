"use client";

import { FormEvent, useState } from "react";
import { Eye, Save } from "lucide-react";
import { api, SourceDefinitionInput } from "@/lib/api";

function slugify(value: string) {
  return value.toLowerCase().replace(/https?:\/\//, "").replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 72);
}

function platformFromInput(url: string, route: string) {
  if (!url.trim()) return route.trim() ? "rsshub" : "custom";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "custom";
  }
}

export default function NewSourcePage() {
  const [url, setUrl] = useState("");
  const [route, setRoute] = useState("");
  const [adapter, setAdapter] = useState("feed");
  const [name, setName] = useState("");
  const [contentType, setContentType] = useState<"paper" | "blog" | "post">("blog");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState("");

  async function runPreview(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    try {
      const data = await api.previewSource({ url: url || undefined, route: route || undefined, adapter, content_type: contentType });
      setPreview(data);
      if (!name && Array.isArray(data.entries) && data.entries[0]) {
        setName(String((data.entries[0] as Record<string, unknown>).title || ""));
      }
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function saveSource() {
    setMessage("");
    const id = slugify(name || route || url);
    if (!id) {
      setMessage("Name, URL, or RSSHub route is required.");
      return;
    }
    const source: SourceDefinitionInput = {
      id,
      title: name || id,
      kind: contentType,
      platform: platformFromInput(url, route),
      homepage: url,
      group: contentType === "paper" ? "Papers" : contentType === "post" ? "Posts" : "Blogs",
      priority: 100,
      language: "auto",
      tags: [contentType],
      fetch: {
        strategy: "first_success",
        interval_seconds: 3600,
        attempts: [{ adapter: adapter as "feed" | "rsshub" | "html_index" | "page_index", url, route, timeout_seconds: 20 }],
      },
      fulltext: { mode: contentType === "blog" ? "detail_only" : "feed_only", max_detail_pages_per_run: 20 },
      summary: { auto: contentType !== "paper", window_days: 7 },
      tagging: { mode: "llm", max_tags: 5 },
      filters: { include_keywords: [], exclude_keywords: [] },
      auth: { mode: "none" },
      stability: "user",
    };
    try {
      const created = await api.createSource(source);
      setMessage(`Saved ${created.id}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <header className="pageHead">
        <div>
          <h1>New Source</h1>
          <span className="subtle">Preview before save; RSSHub route can be relative, like /twitter/user/foo.</span>
        </div>
      </header>
      <form className="panel stack" onSubmit={runPreview}>
        <div className="grid2">
          <div className="field">
            <label htmlFor="new-source-url">URL</label>
            <input id="new-source-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/feed.xml or homepage" />
          </div>
          <div className="field">
            <label htmlFor="new-source-route">RSSHub route</label>
            <input id="new-source-route" value={route} onChange={(e) => setRoute(e.target.value)} placeholder="/route/name" />
          </div>
        </div>
        <div className="grid3">
          <div className="field">
            <label htmlFor="new-source-adapter">Adapter</label>
            <select id="new-source-adapter" value={adapter} onChange={(e) => setAdapter(e.target.value)}>
              <option value="feed">RSS / Atom</option>
              <option value="rsshub">RSSHub</option>
              <option value="html_index">HTML fallback</option>
              <option value="page_index">Page index</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="new-source-content-type">Type</label>
            <select id="new-source-content-type" value={contentType} onChange={(e) => setContentType(e.target.value as "paper" | "blog" | "post")}>
              <option value="paper">Paper</option>
              <option value="blog">Blog</option>
              <option value="post">Post</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="new-source-name">Name</label>
            <input id="new-source-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        </div>
        <div className="actions">
          <button className="button primary" type="submit">
            <Eye size={16} /> Preview
          </button>
          <button className="button" type="button" onClick={saveSource} disabled={!preview}>
            <Save size={16} /> Save
          </button>
        </div>
      </form>
      {message && <pre className="pre">{message}</pre>}
      {preview && <pre className="pre">{JSON.stringify(preview, null, 2)}</pre>}
    </div>
  );
}
