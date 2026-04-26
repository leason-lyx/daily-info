"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Download, Edit3, Play, Plus, RefreshCcw, Save, Upload, X } from "lucide-react";
import { api, Source, SourceAttempt } from "@/lib/api";

type SourceEditForm = {
  name: string;
  content_type: Source["content_type"];
  platform: string;
  homepage_url: string;
  group: string;
  priority: string;
  poll_interval: string;
  auto_summary_enabled: boolean;
  auto_summary_days: string;
  language_hint: string;
  include_keywords: string;
  exclude_keywords: string;
  default_tags: string;
  auth_mode: string;
  stability_level: string;
  attempts_json: string;
  fulltext_json: string;
};

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [message, setMessage] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<SourceEditForm | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");

  async function reload() {
    try {
      setSources(await api.getSources());
    } catch (err) {
      setMessage(`Could not load sources: ${errorMessage(err)}`);
    }
  }

  useEffect(() => {
    let alive = true;
    api.getSources()
      .then((rows) => {
        if (alive) setSources(rows);
      })
      .catch((err) => {
        if (alive) setMessage(`Could not load sources: ${errorMessage(err)}`);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function toggle(source: Source) {
    const actionId = `${source.id}:toggle`;
    setPendingAction(actionId);
    setMessage("");
    try {
      const updated = await api.patchSource(source.id, { enabled: !source.enabled });
      setMessage(`${updated.name} is now ${updated.enabled ? "enabled" : "disabled"}.`);
      await reload();
    } catch (err) {
      setMessage(`Could not update ${source.name}: ${errorMessage(err)}`);
    } finally {
      setPendingAction(null);
    }
  }

  async function fetchNow(source: Source) {
    const actionId = `${source.id}:fetch`;
    const previousRunId = source.latest_run?.id;
    setPendingAction(actionId);
    setMessage("");
    try {
      const result = await api.fetchSource(source.id);
      setMessage(`Queued ${source.name} fetch job ${result.job_id}.`);
      await reload();
      await pollSourceUntilSettled(source.id, previousRunId);
    } catch (err) {
      setMessage(`Could not fetch ${source.name}: ${errorMessage(err)}`);
    } finally {
      setPendingAction(null);
    }
  }

  async function pollSourceUntilSettled(sourceId: string, previousRunId: unknown) {
    for (let i = 0; i < 15; i += 1) {
      await delay(2000);
      const nextSources = await api.getSources();
      setSources(nextSources);
      const current = nextSources.find((item) => item.id === sourceId);
      const run = current?.latest_run;
      if (!run) continue;
      const isNewRun = run.id !== previousRunId;
      const status = typeof run.status === "string" ? run.status : "";
      if (isNewRun && status && status !== "running") return;
    }
  }

  async function exportPack() {
    setPendingAction("export");
    setMessage("");
    try {
      const text = await api.exportSources();
      setMessage(text);
    } catch (err) {
      setMessage(`Could not export sources: ${errorMessage(err)}`);
    } finally {
      setPendingAction(null);
    }
  }

  function startEdit(source: Source) {
    setEditingId(source.id);
    setMessage("");
    setEditForm({
      name: source.name,
      content_type: source.content_type,
      platform: source.platform,
      homepage_url: source.homepage_url,
      group: source.group,
      priority: String(source.priority),
      poll_interval: String(source.poll_interval),
      auto_summary_enabled: source.auto_summary_enabled,
      auto_summary_days: String(source.auto_summary_days || 7),
      language_hint: source.language_hint,
      include_keywords: source.include_keywords.join(", "),
      exclude_keywords: source.exclude_keywords.join(", "),
      default_tags: source.default_tags.join(", "),
      auth_mode: source.auth_mode,
      stability_level: source.stability_level,
      attempts_json: JSON.stringify(source.attempts, null, 2),
      fulltext_json: JSON.stringify(source.fulltext, null, 2),
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setEditForm(null);
    setMessage("");
  }

  async function saveEdit(source: Source) {
    if (!editForm) return;
    setPendingAction(`${source.id}:edit`);
    setMessage("");
    try {
      const attempts = parseAttempts(editForm.attempts_json);
      const fulltext = parseObject(editForm.fulltext_json, "fulltext");
      await api.patchSource(source.id, {
        name: editForm.name.trim(),
        content_type: editForm.content_type,
        platform: editForm.platform.trim(),
        homepage_url: editForm.homepage_url.trim(),
        group: editForm.group.trim(),
        priority: parsePositiveInteger(editForm.priority, "priority"),
        poll_interval: parsePositiveInteger(editForm.poll_interval, "poll interval"),
        auto_summary_enabled: editForm.auto_summary_enabled,
        auto_summary_days: parseAtLeastOne(editForm.auto_summary_days, "summary window days"),
        language_hint: editForm.language_hint.trim(),
        include_keywords: parseCsv(editForm.include_keywords),
        exclude_keywords: parseCsv(editForm.exclude_keywords),
        default_tags: parseCsv(editForm.default_tags),
        auth_mode: editForm.auth_mode.trim(),
        stability_level: editForm.stability_level.trim(),
        attempts,
        fulltext,
      });
      setMessage(`Saved ${source.name}.`);
      setEditingId(null);
      setEditForm(null);
      await reload();
    } catch (err) {
      setMessage(`Could not save ${source.name}: ${errorMessage(err)}`);
    } finally {
      setPendingAction(null);
    }
  }

  async function importPack() {
    setPendingAction("import");
    setMessage("");
    try {
      const result = await api.importSources(importText);
      setMessage(`Imported ${result.imported} sources.`);
      setImportOpen(false);
      setImportText("");
      await reload();
    } catch (err) {
      setMessage(`Could not import sources: ${errorMessage(err)}`);
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div>
      <header className="pageHead">
        <div>
          <h1>Source Registry</h1>
        </div>
        <div className="actions">
          <Link className="button primary" href="/sources/new">
            <Plus size={16} /> New
          </Link>
          <button className="button" onClick={() => setImportOpen((value) => !value)}>
            <Upload size={16} /> Import
          </button>
          <button className="button" onClick={reload}>
            <RefreshCcw size={16} /> Refresh
          </button>
          <button className="button" onClick={exportPack} disabled={pendingAction === "export"}>
            <Download size={16} /> Export
          </button>
        </div>
      </header>
      {importOpen && (
        <section className="panel stack sourceEditor">
          <div className="field">
            <label htmlFor="source-pack-yaml">Source pack YAML</label>
            <textarea id="source-pack-yaml" value={importText} onChange={(event) => setImportText(event.target.value)} placeholder="version: 1&#10;sources: []" />
          </div>
          <div className="actions">
            <button className="button primary" onClick={importPack} disabled={pendingAction === "import" || !importText.trim()}>
              <Upload size={16} /> Import
            </button>
            <button className="button" onClick={() => setImportOpen(false)}>
              <X size={16} /> Cancel
            </button>
          </div>
        </section>
      )}
      <section className="stack">
        {sources.map((source) => (
          <article key={source.id} className="sourceRow">
            <div className="sourceHeader">
              <div className="sourceTitleBlock">
                <h2>{source.name}</h2>
                <SourceMetadata source={source} />
              </div>
              <SourceRunStatus source={source} />
            </div>
            <div className="sourceActions" aria-label={`${source.name} actions`}>
              <button
                className={`sourceSwitch ${source.enabled ? "enabled" : "disabled"}`}
                title={`Toggle ${source.name} source status`}
                aria-label={`Toggle ${source.name} source status. Currently ${source.enabled ? "enabled" : "disabled"}.`}
                aria-pressed={source.enabled}
                onClick={() => toggle(source)}
                disabled={pendingAction === `${source.id}:toggle`}
              >
                <span className="switchKnob" aria-hidden="true" />
                <span className="switchText">
                  {pendingAction === `${source.id}:toggle` ? "saving" : source.enabled ? "enabled" : "disabled"}
                </span>
              </button>
              <button className="button compact" title="Fetch now" onClick={() => fetchNow(source)} disabled={pendingAction === `${source.id}:fetch`}>
                <Play size={16} />
                {pendingAction === `${source.id}:fetch` ? "Queueing..." : "Fetch now"}
              </button>
              <button className="button compact" title="Edit source" onClick={() => startEdit(source)} disabled={pendingAction === `${source.id}:edit`}>
                <Edit3 size={16} /> Edit
              </button>
            </div>
            {editingId === source.id && editForm && (
              <form className="sourceEditor stack" onSubmit={(event) => event.preventDefault()}>
                <div className="grid3">
                  <TextField id="source-edit-name" label="Name" value={editForm.name} onChange={(value) => setEditForm({ ...editForm, name: value })} />
                  <div className="field">
                    <label htmlFor="source-edit-content-type">Type</label>
                    <select id="source-edit-content-type" value={editForm.content_type} onChange={(event) => setEditForm({ ...editForm, content_type: event.target.value as Source["content_type"] })}>
                      <option value="paper">Paper</option>
                      <option value="blog">Blog</option>
                      <option value="post">Post</option>
                    </select>
                  </div>
                  <TextField id="source-edit-platform" label="Platform" value={editForm.platform} onChange={(value) => setEditForm({ ...editForm, platform: value })} />
                </div>
                <div className="grid3">
                  <TextField id="source-edit-homepage-url" label="Homepage URL" value={editForm.homepage_url} onChange={(value) => setEditForm({ ...editForm, homepage_url: value })} />
                  <TextField id="source-edit-group" label="Group" value={editForm.group} onChange={(value) => setEditForm({ ...editForm, group: value })} />
                  <TextField id="source-edit-language-hint" label="Language hint" value={editForm.language_hint} onChange={(value) => setEditForm({ ...editForm, language_hint: value })} />
                </div>
                <div className="grid3">
                  <TextField id="source-edit-priority" label="Priority" value={editForm.priority} onChange={(value) => setEditForm({ ...editForm, priority: value })} />
                  <TextField id="source-edit-poll-interval" label="Poll interval" value={editForm.poll_interval} onChange={(value) => setEditForm({ ...editForm, poll_interval: value })} />
                  <TextField id="source-edit-auth-mode" label="Auth mode" value={editForm.auth_mode} onChange={(value) => setEditForm({ ...editForm, auth_mode: value })} />
                </div>
                <div className="grid3">
                  <TextField id="source-edit-stability-level" label="Stability level" value={editForm.stability_level} onChange={(value) => setEditForm({ ...editForm, stability_level: value })} />
                  <TextField id="source-edit-summary-window-days" label="Summary window days" value={editForm.auto_summary_days} onChange={(value) => setEditForm({ ...editForm, auto_summary_days: value })} />
                  <label className="checkLine">
                    <input
                      type="checkbox"
                      checked={editForm.auto_summary_enabled}
                      onChange={(event) => setEditForm({ ...editForm, auto_summary_enabled: event.target.checked })}
                    />
                    <span>Auto AI summary</span>
                  </label>
                </div>
                <div className="grid2">
                  <TextField id="source-edit-default-tags" label="Default tags" value={editForm.default_tags} onChange={(value) => setEditForm({ ...editForm, default_tags: value })} />
                </div>
                <div className="grid2">
                  <TextField id="source-edit-include-keywords" label="Include keywords" value={editForm.include_keywords} onChange={(value) => setEditForm({ ...editForm, include_keywords: value })} />
                  <TextField id="source-edit-exclude-keywords" label="Exclude keywords" value={editForm.exclude_keywords} onChange={(value) => setEditForm({ ...editForm, exclude_keywords: value })} />
                </div>
                <div className="grid2">
                  <div className="field">
                    <label htmlFor="source-edit-attempts-json">Attempts JSON</label>
                    <textarea id="source-edit-attempts-json" value={editForm.attempts_json} onChange={(event) => setEditForm({ ...editForm, attempts_json: event.target.value })} />
                  </div>
                  <div className="field">
                    <label htmlFor="source-edit-fulltext-json">Fulltext JSON</label>
                    <textarea id="source-edit-fulltext-json" value={editForm.fulltext_json} onChange={(event) => setEditForm({ ...editForm, fulltext_json: event.target.value })} />
                  </div>
                </div>
                <div className="actions">
                  <button className="button primary" onClick={() => saveEdit(source)} disabled={pendingAction === `${source.id}:edit`}>
                    <Save size={16} /> Save
                  </button>
                  <button className="button" onClick={cancelEdit}>
                    <X size={16} /> Cancel
                  </button>
                </div>
              </form>
            )}
          </article>
        ))}
      </section>
      {message && <pre className="pre">{message}</pre>}
    </div>
  );
}

function TextField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function SourceMetadata({ source }: { source: Source }) {
  const auditStatus = String(source.content_audit?.status || "unknown");
  const ownership = source.is_builtin ? "Default pack" : "Custom source";
  const summary = source.auto_summary_enabled ? `Summary auto on, ${source.auto_summary_days || 7}d` : "Summary manual";
  const attemptLabel = `${source.attempts.length} ${source.attempts.length === 1 ? "attempt" : "attempts"}`;
  return (
    <div className="sourceDetails">
      <div className="sourceDescriptor">
        <span className="monoValue">{source.id}</span>
        <span>{source.content_type}</span>
        <span>{source.platform || "unknown platform"}</span>
        <span>{source.group || "General"}</span>
      </div>
      <div className="sourceFacts">
        <span>{ownership}</span>
        <span>Pipeline {auditStatus}</span>
        <span>{summary}</span>
        <span>{attemptLabel}</span>
      </div>
    </div>
  );
}

function SourceRunStatus({ source }: { source: Source }) {
  const latest = latestRunParts(source.latest_run);
  return (
    <div className={`sourceRunStatus ${latest.tone}`} title={latest.title}>
      <div className="sourceRunHead">
        <span className="sourceRunLabel">Last run</span>
        <span className="sourceRunTime">{latest.timeLabel}</span>
      </div>
      <div className="sourceRunBody">
        <span className="statusDot" aria-hidden="true" />
        <span className="sourceRunState">{latest.status}</span>
        <span className="sourceRunDetail">{latest.detail}</span>
      </div>
      {latest.error && <span className="sourceRunError">{latest.error}</span>}
    </div>
  );
}

function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function latestRunParts(run: Record<string, unknown> | null | undefined) {
  if (!run) {
    return {
      status: "never",
      tone: "warn",
      detail: "No runs yet",
      timeLabel: "Never fetched",
      title: "This source has not been fetched yet.",
      error: "",
    };
  }
  const status = typeof run.status === "string" ? run.status : "unknown";
  const rawCount = typeof run.raw_count === "number" ? run.raw_count : 0;
  const itemCount = typeof run.item_count === "number" ? run.item_count : 0;
  const finishedAt = typeof run.finished_at === "string" ? run.finished_at : "";
  const startedAt = typeof run.started_at === "string" ? run.started_at : "";
  const timestamp = finishedAt || startedAt;
  const runDate = parseApiDate(timestamp);
  const tone = status === "succeeded" ? "good" : status === "failed" ? "bad" : "warn";
  const detail = status === "succeeded" ? `${rawCount} fetched, ${itemCount} new` : `${rawCount} fetched, ${itemCount} new`;
  const errorMessage = typeof run.error_message === "string" ? run.error_message : "";
  return {
    status,
    tone,
    detail,
    timeLabel: relativeTime(runDate),
    title: runDate ? runDate.toLocaleString() : "No timestamp recorded.",
    error: status === "failed" ? truncate(errorMessage, 140) : "",
  };
}

function parseApiDate(value: string) {
  if (!value) return null;
  const hasTimeZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  const normalized = hasTimeZone ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeTime(value: Date | null) {
  if (!value) return "No time recorded";
  const timestamp = value.getTime();
  if (Number.isNaN(timestamp)) return "Unknown time";
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const absSeconds = Math.abs(seconds);
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, unitSeconds] of units) {
    if (absSeconds >= unitSeconds) return formatter.format(Math.round(seconds / unitSeconds), unit);
  }
  return formatter.format(seconds, "second");
}

function truncate(value: string, length: number) {
  if (value.length <= length) return value;
  return `${value.slice(0, length - 3)}...`;
}

function parseCsv(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function parsePositiveInteger(value: string, label: string) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) throw new Error(`${label} must be a non-negative integer`);
  return parsed;
}

function parseAtLeastOne(value: string, label: string) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) throw new Error(`${label} must be at least 1`);
  return parsed;
}

function parseObject(value: string, label: string) {
  const parsed = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object`);
  return parsed as Record<string, unknown>;
}

function parseAttempts(value: string) {
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) throw new Error("attempts must be a JSON array");
  return parsed.map((attempt) => {
    const row = attempt as SourceAttempt;
    return {
      kind: row.kind || "direct",
      adapter: row.adapter || "feed",
      url: row.url || "",
      route: row.route || "",
      priority: Number(row.priority || 0),
      enabled: row.enabled !== false,
      config: row.config || {},
    };
  });
}
