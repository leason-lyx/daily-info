"use client";

import { useEffect, useState } from "react";
import { RefreshCcw } from "lucide-react";
import { api, Health, HealthJob, LlmUsage } from "@/lib/api";

export default function HealthPage() {
  const [health, setHealth] = useState<Health>({});
  const [error, setError] = useState("");

  function reload() {
    api.health().then((data) => setHealth(data as Health)).catch((err: Error) => setError(err.message));
  }

  useEffect(reload, []);
  const providerAvailable = Boolean(health.ai_provider?.available);
  const llmUsage = health.ai_provider?.usage as LlmUsage | undefined;
  const jobCounts = health.jobs?.counts;
  const activeJobs = health.jobs?.active || [];
  const recentJobs = health.jobs?.recent || [];

  return (
    <div>
      <header className="pageHead">
        <div>
          <h1>Health</h1>
        </div>
        <button className="iconButton" onClick={reload} title="Refresh health" aria-label="Refresh health">
          <RefreshCcw size={16} />
        </button>
      </header>
      {error && <div className="empty">{error}</div>}
      <section className="grid3">
        <div className="metricRow">
          <div>
            <span className="subtle">Items</span>
            <h2>{health.items_total ?? 0}</h2>
            <span className="subtle">{health.items_24h ?? 0} in 24h</span>
          </div>
        </div>
        <div className="metricRow">
          <div>
            <span className="subtle">Jobs</span>
            <h2>{jobCounts?.queued ?? 0} queued</h2>
            <div className="meta" style={{ marginTop: 8 }}>
              <span>{jobCounts?.running ?? 0} running</span>
              <span>{jobCounts?.retrying ?? 0} retrying</span>
              <span>{jobCounts?.failed ?? 0} failed</span>
            </div>
          </div>
        </div>
        <div className="metricRow">
          <div>
            <span className="subtle">AI Provider</span>
            <h2>{String(health.ai_provider?.type ?? "none")}</h2>
            <span className={providerAvailable ? "badge good" : health.ai_provider?.configured ? "badge warn" : "badge"}>
              {providerAvailable ? "available" : health.ai_provider?.configured ? "configured" : "not configured"}
            </span>
            {health.ai_provider?.last_error ? <span className="sourceError">{String(health.ai_provider.last_error)}</span> : null}
            {llmUsage?.all_time ? (
              <div className="meta" style={{ marginTop: 8 }}>
                <span>{formatCount(llmUsage.all_time.requests)} custom calls</span>
                <span>{formatCount(llmUsage.all_time.total_tokens)} tokens</span>
                <span>{formatCount(llmUsage.recent_24h?.total_tokens)} tokens 24h</span>
              </div>
            ) : null}
          </div>
        </div>
      </section>
      <section className="panel stack healthBlock" style={{ marginTop: 16 }}>
        <div className="row">
          <div>
            <h2>Job Queue</h2>
          </div>
          <span className="badge">{activeJobs.length} active</span>
        </div>
        {activeJobs.length ? (
          <div className="stack">
            {activeJobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </div>
        ) : (
          <div className="empty">No queued or running jobs.</div>
        )}
      </section>
      <section className="panel stack healthBlock" style={{ marginTop: 16 }}>
        <div className="row">
          <div>
            <h2>Recent jobs</h2>
          </div>
          <span className="badge">{recentJobs.length} recent</span>
        </div>
        {recentJobs.length ? (
          <div className="stack">
            {recentJobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </div>
        ) : (
          <div className="empty">No completed jobs yet.</div>
        )}
      </section>
      {!!health.degraded_sources?.length && (
        <section className="panel stack healthBlock">
          <h2>Degraded sources</h2>
          <div className="meta">
            {health.degraded_sources.map((source) => (
              <span className="badge bad" key={String(source.id)}>
                {String(source.name || source.id)}: {String(source.reason)}
              </span>
            ))}
          </div>
        </section>
      )}
      <section className="stack" style={{ marginTop: 16 }}>
        {(health.sources || []).map((source) => {
          const latest = source.latest_run;
          const contentAudit = source.content_audit;
          return (
            <article className="sourceRow" key={String(source.id)}>
              <div>
                <h2>{String(source.name)}</h2>
                <div className="meta">
                  <span>{String(source.id)}</span>
                  <span>{source.enabled ? "enabled" : "disabled"}</span>
                  <span>{source.auto_summary_enabled ? `auto summary ${source.auto_summary_days || 7}d` : "auto summary off"}</span>
                  <span>latest success {formatDate(source.latest_success_at)}</span>
                  <span>raw {String(source.raw_count ?? 0)}</span>
                  <span>items {String(source.item_count ?? 0)}</span>
                  <span>fulltext {formatRate(source.fulltext_success_rate)}</span>
                  <span>summary ready {String(source.summary_ready_count ?? 0)}</span>
                  <span>summary failed {String(source.summary_failed_count ?? 0)}</span>
                  <span>summary fail {formatRate(source.summary_failure_rate)}</span>
                  <span>failures {String(source.consecutive_failures ?? 0)}</span>
                  <span>empty {String(source.consecutive_empty ?? 0)}</span>
                  <span>{String(contentAudit?.status ?? "unknown")}</span>
                </div>
                {latest?.error_message ? <span className="subtle">{String(latest.error_message)}</span> : null}
              </div>
              <span className={latest?.status === "failed" ? "badge bad" : latest?.status === "succeeded" ? "badge good" : "badge"}>
                {String(latest?.status || "never")}
              </span>
            </article>
          );
        })}
      </section>
      <ErrorList title="Recent source errors" rows={health.recent_errors} />
      <ErrorList title="Recent summary errors" rows={health.recent_summary_errors} />
    </div>
  );
}

function ErrorList({ title, rows }: { title: string; rows?: Array<Record<string, unknown>> }) {
  if (!rows?.length) return null;
  return (
    <section className="panel stack healthBlock">
      <div className="row">
        <h2>{title}</h2>
        <span className="badge bad">{rows.length} recent</span>
      </div>
      <div className="stack">
        {rows.map((row, index) => {
          const label = String(row.title || row.source_id || row.item_id || `Error ${index + 1}`);
          const code = typeof row.error_code === "string" && row.error_code ? row.error_code : "failed";
          const timestamp = typeof row.finished_at === "string" ? row.finished_at : typeof row.created_at === "string" ? row.created_at : "";
          return (
            <article className="sourceRow" key={`${label}-${index}`}>
              <div>
                <h2>{label}</h2>
                <div className="meta">
                  {row.source_id ? <span>{String(row.source_id)}</span> : null}
                  {row.provider ? <span>{String(row.provider)}</span> : null}
                  {row.model ? <span>{String(row.model)}</span> : null}
                  <span>{formatDate(timestamp)}</span>
                </div>
                {row.error_message ? <span className="sourceError">{String(row.error_message)}</span> : null}
              </div>
              <span className="badge bad">{code}</span>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function JobRow({ job }: { job: HealthJob }) {
  return (
    <article className="sourceRow">
      <div>
        <div className="row">
          <h2>{job.type}</h2>
          <span className="subtle">#{job.id}</span>
        </div>
        <div className="meta">
          <span>{job.target.label}</span>
          <span>{job.target.kind}</span>
          <span>{jobProgressLabel(job.status)}</span>
          <span>
            attempts {job.attempts}/{job.max_attempts}
          </span>
          <span>scheduled {formatDate(job.scheduled_at)}</span>
          <span>started {formatDate(job.started_at)}</span>
          <span>runtime {formatDuration(job.started_at, job.finished_at)}</span>
        </div>
        {job.error_message ? (
          <span className="sourceError">
            {job.error_code ? `${job.error_code}: ` : ""}
            {job.error_message}
          </span>
        ) : null}
      </div>
      <span className={jobStatusClass(job.status)}>{job.status}</span>
    </article>
  );
}

function jobStatusClass(status: string) {
  if (status === "succeeded") return "badge good";
  if (status === "failed") return "badge bad";
  if (status === "running" || status === "retrying") return "badge warn";
  return "badge";
}

function jobProgressLabel(status: string) {
  if (status === "queued") return "waiting";
  if (status === "running") return "running";
  if (status === "retrying") return "retrying";
  if (status === "succeeded") return "done";
  if (status === "failed") return "failed";
  if (status === "skipped") return "skipped";
  return status;
}

function formatRate(value: number | null | undefined) {
  if (value === null || value === undefined) return "unknown";
  return `${Math.round(value * 100)}%`;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "never";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatCount(value: number | null | undefined) {
  return new Intl.NumberFormat("en").format(value || 0);
}

function formatDuration(startedAt: string | null | undefined, finishedAt: string | null | undefined) {
  if (!startedAt) return "not started";
  const started = new Date(startedAt).getTime();
  const finished = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) return "unknown";
  const seconds = Math.round((finished - started) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}
