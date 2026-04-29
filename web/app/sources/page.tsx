"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, Play, Plus, RefreshCcw, Search } from "lucide-react";
import { api, Source } from "@/lib/api";

type Filters = {
  q: string;
  group: string;
  kind: string;
  platform: string;
  language: string;
};

const EMPTY_FILTERS: Filters = { q: "", group: "", kind: "", platform: "", language: "" };

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [hasLoadedSources, setHasLoadedSources] = useState(false);
  const [isRefreshingSources, setIsRefreshingSources] = useState(false);
  const [pendingActions, setPendingActions] = useState<Set<string>>(() => new Set());

  function isPending(actionId: string) {
    return pendingActions.has(actionId);
  }

  function startPending(actionId: string) {
    setPendingActions((current) => new Set(current).add(actionId));
  }

  function finishPending(actionId: string) {
    setPendingActions((current) => {
      const next = new Set(current);
      next.delete(actionId);
      return next;
    });
  }

  const reload = useCallback(async ({ clearMessageOnSuccess = true, showRefreshing = true } = {}) => {
    if (showRefreshing) setIsRefreshingSources(true);
    try {
      setSources(await api.getSources());
      setHasLoadedSources(true);
      setLoadError("");
      if (clearMessageOnSuccess) setMessage("");
    } catch (err) {
      setLoadError(`Could not load source catalog: ${errorMessage(err)}`);
    } finally {
      if (showRefreshing) setIsRefreshingSources(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api.getSources()
      .then((rows) => {
        if (!alive) return;
        setSources(rows);
        setHasLoadedSources(true);
        setLoadError("");
      })
      .catch((err) => {
        if (alive) setLoadError(`Could not load source catalog: ${errorMessage(err)}`);
      });
    return () => {
      alive = false;
    };
  }, []);

  const facets = useMemo(() => {
    return {
      groups: uniqueSorted(sources.map(sourceGroupName)),
      kinds: uniqueSorted(sources.map((source) => source.kind || source.content_type)),
      platforms: uniqueSorted(sources.map((source) => source.platform).filter(Boolean)),
      languages: uniqueSorted(sources.map((source) => source.language || source.language_hint || "auto")),
    };
  }, [sources]);

  const visibleSources = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    return sources.filter((source) => {
      if (filters.group && sourceGroupName(source) !== filters.group) return false;
      if (filters.kind && (source.kind || source.content_type) !== filters.kind) return false;
      if (filters.platform && source.platform !== filters.platform) return false;
      if (filters.language && (source.language || source.language_hint || "auto") !== filters.language) return false;
      if (!q) return true;
      return [source.title, source.name, source.id, source.platform, source.group, ...(source.default_tags || [])]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [filters, sources]);

  const visibleSourceGroups = useMemo(() => {
    const groups = new Map<string, Source[]>();
    for (const source of visibleSources) {
      const groupName = sourceGroupName(source);
      groups.set(groupName, [...(groups.get(groupName) || []), source]);
    }
    return Array.from(groups.entries())
      .map(([name, groupSources]) => {
        const sortedSources = [...groupSources].sort((a, b) => sourceTitle(a).localeCompare(sourceTitle(b)));
        return {
          name,
          sources: sortedSources,
          subscribedCount: sortedSources.filter((source) => source.subscribed).length,
        };
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [visibleSources]);

  async function toggleSubscription(source: Source) {
    const actionId = `${source.id}:subscription`;
    startPending(actionId);
    setMessage("");
    try {
      if (source.subscribed) {
        await api.unsubscribeSource(source.id);
        setMessage(`Unsubscribed ${source.title || source.name}. It will no longer be fetched or shown in the default feed.`);
      } else {
        await api.subscribeSource(source.id);
        setMessage(`Subscribed ${source.title || source.name}. It is now eligible for fetch and feed display.`);
      }
      await reload({ clearMessageOnSuccess: false });
    } catch (err) {
      setMessage(`Could not update ${source.title || source.name}: ${errorMessage(err)}`);
    } finally {
      finishPending(actionId);
    }
  }

  async function preview(source: Source) {
    const actionId = `${source.id}:preview`;
    startPending(actionId);
    setMessage("");
    try {
      const attempt = source.fetch?.attempts?.[0];
      if (!attempt) throw new Error("This source has no fetch attempts.");
      const result = await api.previewSource({ attempt });
      setMessage(JSON.stringify(result, null, 2));
    } catch (err) {
      setMessage(`Could not preview ${source.title || source.name}: ${errorMessage(err)}`);
    } finally {
      finishPending(actionId);
    }
  }

  async function fetchNow(source: Source) {
    const actionId = `${source.id}:fetch`;
    startPending(actionId);
    setMessage("");
    try {
      const result = await api.fetchSource(source.id);
      setMessage(`Queued ${source.title || source.name} fetch job ${result.job_id}.`);
      await reload({ clearMessageOnSuccess: false });
    } catch (err) {
      setMessage(`Could not fetch ${source.title || source.name}: ${errorMessage(err)}`);
    } finally {
      finishPending(actionId);
    }
  }

  const subscribedCount = sources.filter((source) => source.subscribed).length;
  const isInitialLoading = !hasLoadedSources && !loadError;
  const sourceSummaryText = hasLoadedSources
    ? `${subscribedCount}/${sources.length} subscribed sources feed the default timeline`
    : loadError ? "Sources unavailable" : "Loading sources...";

  return (
    <div>
      <header className="pageHead">
        <div>
          <h1>Source Catalog</h1>
          <span className="subtle">{sourceSummaryText}</span>
        </div>
        <div className="actions">
          <Link className="button primary" href="/sources/new">
            <Plus size={16} /> New
          </Link>
          <button className="button" onClick={() => void reload()} disabled={isRefreshingSources}>
            <RefreshCcw size={16} /> {isRefreshingSources ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>
      {loadError && <div className="empty">{loadError}</div>}

      <section className="toolbar">
        <div className="toolbarPrimary">
          <div className="field">
            <label htmlFor="source-search">Search</label>
            <input id="source-search" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="source, platform, tag" />
          </div>
          <SelectFilter id="source-group-filter" label="Group" value={filters.group} options={facets.groups} onChange={(group) => setFilters({ ...filters, group })} />
          <SelectFilter id="source-kind-filter" label="Kind" value={filters.kind} options={facets.kinds} onChange={(kind) => setFilters({ ...filters, kind })} />
          <SelectFilter id="source-platform-filter" label="Platform" value={filters.platform} options={facets.platforms} onChange={(platform) => setFilters({ ...filters, platform })} />
          <SelectFilter id="source-language-filter" label="Language" value={filters.language} options={facets.languages} onChange={(language) => setFilters({ ...filters, language })} />
        </div>
      </section>

      <section className="stack">
        {isInitialLoading ? <SourcesSkeleton /> : visibleSourceGroups.map((group) => (
          <section className="sourceGroup" key={group.name} aria-labelledby={`source-group-${slugify(group.name)}`}>
            <div className="sourceGroupHead">
              <div>
                <h2 id={`source-group-${slugify(group.name)}`}>{group.name}</h2>
                <span className="subtle">
                  {group.subscribedCount}/{group.sources.length} subscribed
                </span>
              </div>
            </div>
            <div className="sourceGroupRows">
              {group.sources.map((source) => (
                <article key={source.id} className="sourceRow">
                  <div className="sourceHeader">
                    <div className="sourceTitleBlock">
                      <h3>{sourceTitle(source)}</h3>
                      <SourceMetadata source={source} />
                    </div>
                    <SourceRunStatus source={source} />
                  </div>
                  <div className="sourceActions" aria-label={`${sourceTitle(source)} actions`}>
                    <button
                      className={`sourceSwitch ${source.subscribed ? "enabled" : "disabled"}`}
                      title={`${source.subscribed ? "Unsubscribe from" : "Subscribe to"} ${sourceTitle(source)}`}
                      aria-label={`${source.subscribed ? "Unsubscribe from" : "Subscribe to"} ${sourceTitle(source)}`}
                      aria-pressed={source.subscribed}
                      onClick={() => toggleSubscription(source)}
                      disabled={isPending(`${source.id}:subscription`)}
                    >
                      <span className="switchKnob" aria-hidden="true" />
                      <span className="switchText">
                        {isPending(`${source.id}:subscription`) ? "saving" : source.subscribed ? "subscribed" : "available"}
                      </span>
                    </button>
                    <button className="button compact" title="Preview source" onClick={() => preview(source)} disabled={isPending(`${source.id}:preview`)}>
                      <Eye size={16} />
                      {isPending(`${source.id}:preview`) ? "Previewing..." : "Preview"}
                    </button>
                    <button className="button compact" title="Fetch now" onClick={() => fetchNow(source)} disabled={!source.subscribed || isPending(`${source.id}:fetch`)}>
                      <Play size={16} />
                      {isPending(`${source.id}:fetch`) ? "Queueing..." : "Fetch now"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))}
        {!isInitialLoading && hasLoadedSources && !visibleSources.length && !loadError && (
          <div className="empty">
            <Search size={22} /> No sources match current filters.
          </div>
        )}
      </section>
      {message && <pre className="pre">{message}</pre>}
    </div>
  );
}

function SourcesSkeleton() {
  return (
    <>
      {[3, 2].map((rowCount, groupIndex) => (
        <section className="sourceGroup skeletonSourceGroup" aria-hidden="true" key={groupIndex}>
          <div className="sourceGroupHead">
            <div className="skeletonSourceGroupTitle">
              <span className="skeletonLine skeletonSourceGroupName" />
              <span className="skeletonLine skeletonSourceGroupMeta" />
            </div>
          </div>
          <div className="sourceGroupRows">
            {Array.from({ length: rowCount }).map((_, rowIndex) => (
              <article className="sourceRow skeletonSourceRow" key={rowIndex}>
                <div className="sourceHeader">
                  <div className="sourceTitleBlock">
                    <span className="skeletonLine skeletonSourceTitle" />
                    <div className="sourceDetails">
                      <span className="skeletonLine skeletonPill" />
                      <span className="skeletonLine skeletonPill" />
                      <span className="skeletonLine skeletonDate" />
                    </div>
                  </div>
                  <div className="sourceRunStatus skeletonSourceRun">
                    <span className="skeletonLine skeletonSourceRunTop" />
                    <span className="skeletonLine skeletonSourceRunBottom" />
                  </div>
                </div>
                <div className="sourceActions">
                  <span className="skeletonLine skeletonSourceSwitch" />
                  <span className="skeletonLine skeletonSourceButton" />
                  <span className="skeletonLine skeletonSourceButton" />
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

function SelectFilter({ id, label, value, options, onChange }: { id: string; label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">All</option>
        {options.map((option) => (
          <option value={option} key={option}>{option}</option>
        ))}
      </select>
    </div>
  );
}

function SourceMetadata({ source }: { source: Source }) {
  const summary = source.summary?.auto ? `Auto summary · ${source.summary.window_days || 7}d` : "Manual summary";
  const fetchInterval = source.fetch?.interval_seconds || source.poll_interval;
  return (
    <div className="sourceDetails">
      <span className="sourcePill strong">{source.kind || source.content_type}</span>
      {source.platform ? <span className="sourcePill">{source.platform}</span> : null}
      <span className="sourcePill">{sourceGroupName(source)}</span>
      <span className="sourcePill quiet">{formatFetchInterval(fetchInterval)}</span>
      <span className="sourcePill quiet">{summary}</span>
    </div>
  );
}

function SourceRunStatus({ source }: { source: Source }) {
  const latest = latestRunParts(source.latest_run, source.runtime);
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

function latestRunParts(run: Record<string, unknown> | null | undefined, runtime: Source["runtime"]) {
  if (!run) {
    const lastError = runtime?.last_error || "";
    return {
      status: lastError ? "attention" : "never",
      tone: lastError ? "bad" : "warn",
      detail: lastError ? `${runtime?.failure_count || 0} failures` : "No runs yet",
      timeLabel: runtime?.last_run_at ? relativeTime(parseApiDate(runtime.last_run_at)) : "Never fetched",
      title: lastError || "This source has not been fetched yet.",
      error: truncate(lastError, 140),
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
  const errorMessage = typeof run.error_message === "string" ? run.error_message : "";
  return {
    status,
    tone,
    detail: `${rawCount} fetched, ${itemCount} new`,
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

function formatFetchInterval(seconds: number | undefined) {
  if (!seconds || seconds <= 0) return "Interval not set";
  if (seconds % (60 * 60 * 24) === 0) {
    const days = seconds / (60 * 60 * 24);
    return `Every ${days} ${days === 1 ? "day" : "days"}`;
  }
  if (seconds % (60 * 60) === 0) {
    const hours = seconds / (60 * 60);
    return `Every ${hours} ${hours === 1 ? "hour" : "hours"}`;
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `Every ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  }
  return `Every ${seconds} seconds`;
}

function truncate(value: string, length: number) {
  if (value.length <= length) return value;
  return `${value.slice(0, length - 3)}...`;
}

function sourceGroupName(source: Source) {
  return source.group || "Other";
}

function sourceTitle(source: Source) {
  return source.title || source.name || source.id;
}

function slugify(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "other";
}

function uniqueSorted(values: string[]) {
  return Array.from(new Set(values.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}
