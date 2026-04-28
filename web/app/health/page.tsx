"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, ChevronUp, RefreshCcw } from "lucide-react";
import { api, Health, HealthJob, LlmUsage } from "@/lib/api";

const SECTION_LIMITS = {
  jobQueue: 5,
  recentJobs: 5,
  sourceHealth: 6,
  recentSourceErrors: 3,
} as const;

type ExpandableHealthSection = keyof typeof SECTION_LIMITS;

const defaultExpandedSections: Record<ExpandableHealthSection, boolean> = {
  jobQueue: false,
  recentJobs: false,
  sourceHealth: false,
  recentSourceErrors: false,
};

export default function HealthPage() {
  const [health, setHealth] = useState<Health>({});
  const [error, setError] = useState("");
  const [expandedSections, setExpandedSections] = useState(defaultExpandedSections);

  function reload() {
    api.health().then((data) => setHealth(data as Health)).catch((err: Error) => setError(err.message));
  }

  useEffect(reload, []);
  const providerAvailable = Boolean(health.ai_provider?.available);
  const llmUsage = health.ai_provider?.usage as LlmUsage | undefined;
  const jobCounts = health.jobs?.counts;
  const activeJobs = health.jobs?.active || [];
  const recentJobs = health.jobs?.recent || [];
  const sources = health.sources || [];
  const recentSourceErrors = health.recent_errors || [];
  const visibleActiveJobs = expandedSections.jobQueue ? activeJobs : activeJobs.slice(0, SECTION_LIMITS.jobQueue);
  const visibleRecentJobs = expandedSections.recentJobs ? recentJobs : recentJobs.slice(0, SECTION_LIMITS.recentJobs);
  const visibleSources = expandedSections.sourceHealth ? sources : sources.slice(0, SECTION_LIMITS.sourceHealth);

  function toggleSection(section: ExpandableHealthSection) {
    setExpandedSections((current) => ({ ...current, [section]: !current[section] }));
  }

  return (
    <div className="healthPage">
      <header className="pageHead">
        <div>
          <h1>Health</h1>
        </div>
        <button className="iconButton" onClick={reload} title="Refresh health" aria-label="Refresh health">
          <RefreshCcw size={16} />
        </button>
      </header>
      {error && <div className="healthEmpty">{error}</div>}
      <section className="healthOverview" aria-label="Health overview">
        <div className="healthMetric">
          <span className="healthEyebrow">Items</span>
          <strong className="healthMetricValue">{formatCount(health.items_total)}</strong>
          <div className="healthMetricMeta">{formatCount(health.items_24h)} in 24h</div>
        </div>
        <div className="healthMetric">
          <span className="healthEyebrow">Jobs</span>
          <div className="healthMetricValue">
            <strong>{formatCount(jobCounts?.queued)}</strong>
            <span>queued</span>
          </div>
          <div className="healthMetricMeta">
            <span>{formatCount(jobCounts?.running)} running</span>
            <span>{formatCount(jobCounts?.retrying)} retrying</span>
            <span>{formatCount(jobCounts?.failed)} failed</span>
          </div>
        </div>
        <div className="healthMetric">
          <span className="healthEyebrow">AI Provider</span>
          <div className="healthProviderLine">
            <strong className="healthMetricValue">{String(health.ai_provider?.type ?? "none")}</strong>
            <span className={providerAvailable ? "badge good" : health.ai_provider?.configured ? "badge warn" : "badge"}>
              {providerAvailable ? "available" : health.ai_provider?.configured ? "configured" : "not configured"}
            </span>
          </div>
          {health.ai_provider?.last_error ? <span className="sourceError">{String(health.ai_provider.last_error)}</span> : null}
          {llmUsage?.all_time ? (
            <div className="healthMetricMeta">
              <span>{formatCount(llmUsage.all_time.requests)} custom calls</span>
              <span>{formatCount(llmUsage.all_time.total_tokens)} tokens</span>
              <span>{formatCount(llmUsage.recent_24h?.total_tokens)} tokens 24h</span>
            </div>
          ) : null}
        </div>
      </section>

      <HealthSection
        title="Job Queue"
        meta={sectionCountLabel(visibleActiveJobs.length, activeJobs.length, "active")}
        totalCount={activeJobs.length}
        visibleCount={visibleActiveJobs.length}
        expanded={expandedSections.jobQueue}
        onToggle={() => toggleSection("jobQueue")}
        actionLabel={expandedSections.jobQueue ? "Show less" : `Show all ${activeJobs.length}`}
      >
        {activeJobs.length ? (
          <div className="healthList">
            {visibleActiveJobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </div>
        ) : (
          <div className="healthEmpty">No queued or running jobs.</div>
        )}
      </HealthSection>

      <HealthSection
        title="Recent jobs"
        meta={sectionCountLabel(visibleRecentJobs.length, recentJobs.length, "recent")}
        totalCount={recentJobs.length}
        visibleCount={visibleRecentJobs.length}
        expanded={expandedSections.recentJobs}
        onToggle={() => toggleSection("recentJobs")}
        actionLabel={expandedSections.recentJobs ? "Show less" : `Show all ${recentJobs.length}`}
      >
        {recentJobs.length ? (
          <div className="healthList">
            {visibleRecentJobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </div>
        ) : (
          <div className="healthEmpty">No completed jobs yet.</div>
        )}
      </HealthSection>

      {!!health.degraded_sources?.length && (
        <HealthSection title="Degraded sources" meta={`${health.degraded_sources.length} needs attention`}>
          <div className="healthAlertList">
            {health.degraded_sources.map((source) => (
              <span className="healthAlertItem" key={String(source.id)}>
                <strong>{String(source.name || source.id)}</strong>
                <span>{String(source.reason)}</span>
              </span>
            ))}
          </div>
        </HealthSection>
      )}

      <HealthSection
        title="Source health"
        meta={sectionCountLabel(visibleSources.length, sources.length, "sources")}
        totalCount={sources.length}
        visibleCount={visibleSources.length}
        expanded={expandedSections.sourceHealth}
        onToggle={() => toggleSection("sourceHealth")}
        actionLabel={expandedSections.sourceHealth ? "Show less" : `Show all ${sources.length}`}
      >
        <div className="healthList">
          {visibleSources.map((source) => {
            const latest = source.latest_run;
            const contentAudit = source.content_audit;
            return (
              <article className="healthListRow" key={String(source.id)}>
                <div className="healthRowMain">
                  <h3 className="healthRowTitle">{String(source.name)}</h3>
                  <div className="healthMeta">
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
        </div>
      </HealthSection>

      <ErrorList
        title="Recent source errors"
        rows={recentSourceErrors}
        limit={SECTION_LIMITS.recentSourceErrors}
        expanded={expandedSections.recentSourceErrors}
        onToggle={() => toggleSection("recentSourceErrors")}
      />
      <ErrorList title="Recent summary errors" rows={health.recent_summary_errors} />
    </div>
  );
}

function HealthSection({
  title,
  meta,
  children,
  totalCount,
  visibleCount,
  expanded = false,
  onToggle,
  actionLabel,
}: {
  title: string;
  meta: string;
  children: ReactNode;
  totalCount?: number;
  visibleCount?: number;
  expanded?: boolean;
  onToggle?: () => void;
  actionLabel?: string;
}) {
  const canToggle = Boolean(onToggle && totalCount !== undefined && visibleCount !== undefined && (expanded || totalCount > visibleCount));
  const ToggleIcon = expanded ? ChevronUp : ChevronDown;
  return (
    <section className="healthSection">
      <div className="healthSectionHead">
        <div className="healthSectionTitle">
          <span className="healthSectionAccent" aria-hidden="true" />
          <h2>{title}</h2>
        </div>
        <div className="healthSectionTools">
          <span className="healthSectionMeta">{meta}</span>
          {canToggle ? (
            <button className="healthSectionAction" type="button" onClick={onToggle} aria-expanded={expanded}>
              {actionLabel || (expanded ? "Show less" : "Show all")}
              <ToggleIcon size={14} />
            </button>
          ) : null}
        </div>
      </div>
      {children}
    </section>
  );
}

function ErrorList({
  title,
  rows,
  limit,
  expanded = false,
  onToggle,
}: {
  title: string;
  rows?: Array<Record<string, unknown>>;
  limit?: number;
  expanded?: boolean;
  onToggle?: () => void;
}) {
  if (!rows?.length) return null;
  const visibleRows = limit && !expanded ? rows.slice(0, limit) : rows;
  return (
    <HealthSection
      title={title}
      meta={limit ? sectionCountLabel(visibleRows.length, rows.length, "recent") : `${rows.length} recent`}
      totalCount={limit ? rows.length : undefined}
      visibleCount={limit ? visibleRows.length : undefined}
      expanded={expanded}
      onToggle={onToggle}
      actionLabel={expanded ? "Show less" : `Show all ${rows.length}`}
    >
      <div className="healthList">
        {visibleRows.map((row, index) => {
          const label = String(row.title || row.source_id || row.item_id || `Error ${index + 1}`);
          const code = typeof row.error_code === "string" && row.error_code ? row.error_code : "failed";
          const timestamp = typeof row.finished_at === "string" ? row.finished_at : typeof row.created_at === "string" ? row.created_at : "";
          return (
            <article className="healthListRow" key={`${label}-${index}`}>
              <div className="healthRowMain">
                <h3 className="healthRowTitle">{label}</h3>
                <div className="healthMeta">
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
    </HealthSection>
  );
}

function sectionCountLabel(visibleCount: number, totalCount: number, label: string) {
  if (totalCount > visibleCount) return `${visibleCount} of ${totalCount} ${label}`;
  return `${totalCount} ${label}`;
}

function JobRow({ job }: { job: HealthJob }) {
  return (
    <article className="healthListRow">
      <div className="healthRowMain">
        <div className="healthRowTitleLine">
          <h3 className="healthRowTitle">{job.type}</h3>
          <span className="subtle">#{job.id}</span>
        </div>
        <div className="healthMeta">
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
