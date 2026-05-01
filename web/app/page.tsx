"use client";

import { Suspense } from "react";
import { useEffect, useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Check, ExternalLink, Eye, EyeOff, RefreshCcw, Search, Sparkles, Star } from "lucide-react";
import { api, Item, Source } from "@/lib/api";

const NO_SOURCE_SENTINEL = "__none__";
const CONTENT_TYPE_LABELS: Record<string, string> = {
  paper: "Paper",
  blog: "Blog",
  post: "Post",
};
const SOURCE_GROUP_ORDER = ["Papers", "Model Labs", "Engineering Blogs", "AI News", "Tech Media", "Post", "General"];

function statusClass(status: string) {
  if (status === "ready") return "badge good";
  if (status === "failed") return "badge bad";
  if (status === "pending") return "badge warn";
  return "badge";
}

function summaryStatusLabel(status: string) {
  if (status === "not_configured") return "AI summary off";
  if (status === "pending") return "Summarizing";
  if (status === "ready") return "AI summary ready";
  if (status === "failed") return "Summary failed";
  if (status === "skipped") return "Summary skipped";
  return status.replaceAll("_", " ");
}

function summaryStatusTitle(status: string) {
  if (status === "not_configured") return "AI summary provider is not configured.";
  if (status === "pending") return "This item is waiting for AI summarization.";
  if (status === "ready") return "AI summary is available for this item.";
  if (status === "failed") return "AI summarization failed for this item.";
  if (status === "skipped") return "AI summarization was skipped for this item.";
  return "AI summary status.";
}

function formatPublishedAt(value: string | null) {
  if (!value) return "No date";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function visibleAuthors(authors: string[]) {
  const normalized = authors.flatMap((author) => author.split(/\s*,\s*/)).map((author) => author.trim()).filter(Boolean);
  const shown = normalized.slice(0, 3);
  const hiddenCount = Math.max(normalized.length - shown.length, 0);
  return { shown, hiddenCount };
}

function summaryText(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (value) return [String(value)];
  return [];
}

function aiSummaryRows(item: Item) {
  const data = item.ai_summary || {};
  if (!data.one_sentence) return [];
  const fieldsByType: Record<string, Array<[string, string]>> = {
    paper: [
      ["研究问题", "research_question"],
      ["方法", "method"],
      ["关键结果", "key_results"],
      ["局限", "limitations"],
      ["为什么重要", "why_it_matters"],
    ],
    blog: [
      ["发生了什么", "what_happened"],
      ["要点", "key_takeaways"],
      ["适合谁读", "who_should_read"],
      ["注意事项", "caveats"],
      ["为什么重要", "why_it_matters"],
    ],
    post: [
      ["核心观点", "main_update_or_claim"],
      ["上下文", "context"],
      ["信号类型", "signal_type"],
      ["主观性提示", "subjectivity_notice"],
      ["为什么重要", "why_it_matters"],
    ],
  };
  return (fieldsByType[item.content_type] || fieldsByType.blog)
    .map(([label, key]) => ({ label, values: summaryText(data[key]) }))
    .filter((row) => row.values.length);
}

function summarizeButtonLabel(status: string) {
  if (status === "pending") return "生成中";
  if (status === "ready") return "重新生成摘要";
  return "生成中文摘要";
}

function readStatusLabel(read: boolean) {
  return read ? "已读" : "未读";
}

function readButtonLabel(read: boolean) {
  return read ? "标为未读" : "标为已读";
}

function fallbackSnippet(item: Item) {
  const text = item.summary || item.raw_text || "";
  return text ? `${text.slice(0, 360)}${text.length > 360 ? "..." : ""}` : "No summary text available yet.";
}

function itemSourceRows(item: Item) {
  const rows = item.sources?.length ? item.sources : [{ source_id: item.source_id, source_name: item.source_name, url: item.url, tags: item.tags }];
  return rows.filter((source) => source.source_id || source.source_name);
}

function sourceTitle(item: Item) {
  const rows = itemSourceRows(item);
  if (rows.length <= 1) return item.source_name;
  return rows.map((source) => source.source_name || source.source_id).join("\n");
}

function selectedIdsFromParams(sourceRows: Source[], sourceParams: string[]) {
  if (sourceParams.includes(NO_SOURCE_SENTINEL)) return new Set<string>();
  if (sourceParams.length) return new Set(sourceParams);
  return new Set(sourceRows.map((source) => source.id));
}

function sourceIdsForItemsQuery(sourceRows: Source[], sourceParams: string[]) {
  if (sourceParams.includes(NO_SOURCE_SENTINEL)) return [];
  if (sourceParams.length) return sourceRows.map((source) => source.id).filter((id) => sourceParams.includes(id));
  return sourceRows.map((source) => source.id);
}

function sourceGroupName(source: Source) {
  return source.group || CONTENT_TYPE_LABELS[source.content_type] || "General";
}

function sourceGroupRank(groupName: string) {
  const index = SOURCE_GROUP_ORDER.indexOf(groupName);
  return index === -1 ? SOURCE_GROUP_ORDER.length : index;
}

function itemQueryFromFilters(searchParams: URLSearchParams, sourceRows: Source[]) {
  const next = new URLSearchParams(searchParams.toString());
  const sourceParams = next.getAll("source_id");
  next.delete("hidden");
  next.delete("source_id");
  const selectedSourceIds = sourceIdsForItemsQuery(sourceRows, sourceParams);
  if (!selectedSourceIds.length) {
    next.append("source_id", NO_SOURCE_SENTINEL);
  } else if (selectedSourceIds.length < sourceRows.length) {
    selectedSourceIds.forEach((sourceId) => next.append("source_id", sourceId));
  }
  return next;
}

function FeedView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState("");
  const [loadedQueryKey, setLoadedQueryKey] = useState<string | null>(null);
  const [failedQueryKey, setFailedQueryKey] = useState<string | null>(null);
  const [summarizingIds, setSummarizingIds] = useState<Set<string>>(new Set());
  const [isPending, startTransition] = useTransition();
  const queryKey = searchParams.toString();
  const query = useMemo(() => new URLSearchParams(queryKey), [queryKey]);
  const isLoading = loadedQueryKey !== queryKey && failedQueryKey !== queryKey;
  const activeError = failedQueryKey === queryKey ? error : "";
  const searchQuery = searchParams.get("q") || "";
  const sourceParams = searchParams.getAll("source_id");
  const selectedSourceIds = useMemo(() => {
    return selectedIdsFromParams(sources, sourceParams);
  }, [sourceParams, sources]);
  const sourceGroups = useMemo(() => {
    const groups = new Map<string, { groupName: string; sources: Source[] }>();
    for (const source of sources) {
      const groupName = sourceGroupName(source);
      const group = groups.get(groupName) || { groupName, sources: [] };
      group.sources.push(source);
      groups.set(groupName, group);
    }
    return Array.from(groups.values())
      .map((group) => ({
        ...group,
        sources: group.sources.sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => {
        return sourceGroupRank(a.groupName) - sourceGroupRank(b.groupName) || a.groupName.localeCompare(b.groupName);
      });
  }, [sources]);

  useEffect(() => {
    let alive = true;

    async function loadFeed() {
      try {
        const sourceRows = await api.getSources();
        if (!alive) return;
        const subscribedRows = sourceRows.filter((source) => source.subscribed);
        setSources(subscribedRows);
        const feed = await api.getItems(itemQueryFromFilters(query, subscribedRows));
        if (!alive) return;
        setItems(feed.items);
        setTotal(feed.total);
        setError("");
        setFailedQueryKey(null);
        setLoadedQueryKey(queryKey);
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Failed to load feed.");
        setFailedQueryKey(queryKey);
      }
    }

    void loadFeed();
    return () => {
      alive = false;
    };
  }, [query, queryKey]);

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    replaceQuery(next);
  }

  function setSourceFilter(nextSourceIds: string[]) {
    const next = new URLSearchParams(searchParams.toString());
    next.delete("source_id");
    if (!nextSourceIds.length) {
      next.append("source_id", NO_SOURCE_SENTINEL);
    } else if (nextSourceIds.length < sources.length) {
      nextSourceIds.forEach((sourceId) => next.append("source_id", sourceId));
    }
    replaceQuery(next);
  }

  function toggleSource(sourceId: string) {
    const nextSelected = new Set(selectedSourceIds);
    if (nextSelected.has(sourceId)) nextSelected.delete(sourceId);
    else nextSelected.add(sourceId);
    setSourceFilter(sources.map((source) => source.id).filter((id) => nextSelected.has(id)));
  }

  function toggleSourceGroup(groupSources: Source[]) {
    const groupIds = groupSources.map((source) => source.id);
    const allGroupSelected = groupIds.every((id) => selectedSourceIds.has(id));
    const nextSelected = new Set(selectedSourceIds);
    for (const sourceId of groupIds) {
      if (allGroupSelected) nextSelected.delete(sourceId);
      else nextSelected.add(sourceId);
    }
    setSourceFilter(sources.map((source) => source.id).filter((id) => nextSelected.has(id)));
  }

  function replaceQuery(next: URLSearchParams) {
    next.delete("hidden");
    const queryString = next.toString();
    startTransition(() => router.replace(queryString ? `${pathname}?${queryString}` : pathname));
  }

  async function itemAction(item: Item, action: "read" | "star" | "resummarize") {
    const updated = action === "resummarize" ? await api.resummarize(item.id) : await api.markItem(item.id, action);
    setItems((rows) => rows.map((row) => (row.id === item.id ? updated : row)));
    if (action === "resummarize" && updated.summary_status === "pending") {
      pollItemSummary(item.id);
    }
  }

  async function pollItemSummary(itemId: string) {
    setSummarizingIds((ids) => new Set(ids).add(itemId));
    try {
      for (let i = 0; i < 20; i += 1) {
        await delay(2000);
        const updated = await api.getItem(itemId);
        setItems((rows) => rows.map((row) => (row.id === itemId ? updated : row)));
        if (updated.summary_status === "ready" || updated.summary_status === "failed" || updated.summary_status === "not_configured") return;
      }
    } finally {
      setSummarizingIds((ids) => {
        const next = new Set(ids);
        next.delete(itemId);
        return next;
      });
    }
  }

  return (
    <div>
      <header className="pageHead">
        <div>
          <h1>Unified Feed</h1>
          <span className="subtle">{isLoading && !items.length ? "Loading items..." : `${total} items match current filters`}</span>
        </div>
      </header>

      <section className="toolbar">
        <div className="toolbarPrimary">
          <div className="field">
            <label htmlFor="feed-search">Search</label>
            <input
              id="feed-search"
              key={searchQuery}
              defaultValue={searchQuery}
              placeholder="title, summary, author, tag"
              onBlur={(e) => setParam("q", e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  setParam("q", e.currentTarget.value);
                }
              }}
            />
          </div>
          <div className="field">
            <label htmlFor="feed-window">Window</label>
            <select id="feed-window" value={searchParams.get("since") || ""} onChange={(e) => setParam("since", e.target.value)}>
              <option value="">Any time</option>
              <option value="today">Today</option>
              <option value="3d">Past 3 days</option>
              <option value="7d">Past 7 days</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="feed-summary-status">Summary</label>
            <select id="feed-summary-status" value={searchParams.get("summary_status") || ""} onChange={(e) => setParam("summary_status", e.target.value)}>
              <option value="">Any</option>
              <option value="not_configured">AI summary off</option>
              <option value="pending">Summarizing</option>
              <option value="ready">AI summary ready</option>
              <option value="failed">Summary failed</option>
            </select>
          </div>
        </div>
        <div className="sourceFilterField">
          <div className="sourceFilterHead">
            <label>Type / Source</label>
            <div className="sourceFilterTools">
              <button type="button" onClick={() => setSourceFilter(sources.map((source) => source.id))} disabled={!sources.length}>All</button>
              <button type="button" onClick={() => setSourceFilter([])} disabled={!sources.length}>None</button>
            </div>
          </div>
          <div className="sourceTypeGrid">
            {sourceGroups.map((group) => {
              const selectedCount = group.sources.filter((source) => selectedSourceIds.has(source.id)).length;
              const allSelected = selectedCount === group.sources.length;
              const noneSelected = selectedCount === 0;
              return (
                <div className="sourceTypeGroup" key={group.groupName}>
                  <div className="sourceTypeHead">
                    <label className="sourceTypeToggle">
                      <input type="checkbox" checked={allSelected} onChange={() => toggleSourceGroup(group.sources)} />
                      <span className={allSelected ? "checkBox checked" : "checkBox"} aria-hidden="true">
                        {allSelected && <Check size={14} />}
                      </span>
                      <span>{group.groupName}</span>
                    </label>
                    <div className="sourceTypeActions">
                      <span className={noneSelected ? "badge warn" : "badge"}>{selectedCount}/{group.sources.length}</span>
                      <button type="button" onClick={() => {
                        const nextSelected = new Set(selectedSourceIds);
                        group.sources.forEach((source) => nextSelected.add(source.id));
                        setSourceFilter(sources.map((source) => source.id).filter((id) => nextSelected.has(id)));
                      }}>All</button>
                      <button type="button" onClick={() => {
                        const nextSelected = new Set(selectedSourceIds);
                        group.sources.forEach((source) => nextSelected.delete(source.id));
                        setSourceFilter(sources.map((source) => source.id).filter((id) => nextSelected.has(id)));
                      }}>None</button>
                    </div>
                  </div>
                  <div className="sourceOptionList">
                    {group.sources.map((source) => {
                      const checked = selectedSourceIds.has(source.id);
                      return (
                        <label key={source.id} className="sourceOption">
                          <input type="checkbox" checked={checked} onChange={() => toggleSource(source.id)} />
                          <span className={checked ? "checkBox checked" : "checkBox"} aria-hidden="true">
                            {checked && <Check size={14} />}
                          </span>
                          <span className="sourceOptionText">
                            <span>{source.name}</span>
                            <span>{source.platform || source.group || source.id}</span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {activeError && <div className="empty">{activeError}</div>}
      {(isPending || (isLoading && items.length > 0)) && <div className="subtle">Refreshing...</div>}
      <section className="stack">
        {isLoading && !items.length ? <FeedSkeleton /> : items.map((item) => {
          const authors = visibleAuthors(item.authors);
          const aiRows = aiSummaryRows(item);
          const hasAiSummary = Boolean(item.ai_summary?.one_sentence);
          const summarizeDisabled = item.summary_status === "pending" || summarizingIds.has(item.id);
          const itemSources = itemSourceRows(item);
          const shownSources = itemSources.slice(0, 2);
          const hiddenSourceCount = Math.max(itemSources.length - shownSources.length, 0);

          return (
            <article className="item" key={item.id}>
              <div className="itemTop">
                <div>
                  <h2>{item.chinese_title || item.title}</h2>
                  {item.chinese_title && <span className="subtle">{item.title}</span>}
                </div>
                <span className={statusClass(item.summary_status)} title={summaryStatusTitle(item.summary_status)}>
                  {summaryStatusLabel(item.summary_status)}
                </span>
              </div>
              <div className="itemMetaBlock">
                <div className="metaPrimary">
                  <span className="sourceLinks" title={sourceTitle(item)}>
                    {shownSources.map((source) => {
                      const label = source.source_name || source.source_id;
                      return source.url ? (
                        <a className="metaSource" href={source.url} target="_blank" rel="noreferrer" key={source.source_id}>
                          {label}
                        </a>
                      ) : (
                        <span className="metaSource" key={source.source_id}>{label}</span>
                      );
                    })}
                    {hiddenSourceCount > 0 && <span className="metaSource muted">+{hiddenSourceCount} more</span>}
                  </span>
                  <span className="metaPill">{item.content_type}</span>
                  <span className="metaPill">{item.platform || "unknown platform"}</span>
                  <time className="metaTime" dateTime={item.published_at || undefined}>
                    {formatPublishedAt(item.published_at)}
                  </time>
                </div>
                {authors.shown.length > 0 && (
                  <div className="metaAuthors">
                    <span className="metaAuthorsLabel">By</span>
                    <span>{authors.shown.join(", ")}</span>
                    {authors.hiddenCount > 0 && <span className="metaMore">+{authors.hiddenCount} more</span>}
                  </div>
                )}
              </div>
              {hasAiSummary ? (
                <section className="aiSummary">
                  <div className="aiSummaryHead">
                    <Sparkles size={16} />
                    <strong>AI 中文摘要</strong>
                  </div>
                  <p>{String(item.ai_summary?.one_sentence || "")}</p>
                  {aiRows.slice(0, 3).map((row) => (
                    <div className="aiSummaryRow" key={row.label}>
                      <span>{row.label}</span>
                      <div>
                        {row.values.map((value) => (
                          <p key={value}>{value}</p>
                        ))}
                      </div>
                    </div>
                  ))}
                </section>
              ) : (
                <p>{fallbackSnippet(item)}</p>
              )}
              <div className="meta">
                {item.tags.map((tag) => (
                  <span className="badge" key={tag}>
                    {tag}
                  </span>
                ))}
                {item.entities.slice(0, 8).map((entity) => (
                  <span className="badge" key={entity}>
                    {entity}
                  </span>
                ))}
              </div>
              <div className="actions">
                <a className="button" href={item.url} target="_blank" rel="noreferrer">
                  <ExternalLink size={16} /> Original
                </a>
                <span className={item.read ? "badge readStatus readStatusDone" : "badge readStatus"}>{readStatusLabel(item.read)}</span>
                <button
                  className="button readToggleButton"
                  title={readButtonLabel(item.read)}
                  aria-label={readButtonLabel(item.read)}
                  onClick={() => itemAction(item, "read")}
                >
                  {item.read ? <EyeOff size={16} /> : <Eye size={16} />}
                  {readButtonLabel(item.read)}
                </button>
                <button
                  className="iconButton"
                  title={item.starred ? "Remove star" : "Star item"}
                  aria-label={item.starred ? "Remove star" : "Star item"}
                  onClick={() => itemAction(item, "star")}
                >
                  <Star size={16} fill={item.starred ? "currentColor" : "none"} />
                </button>
                <button className="button" title={summarizeButtonLabel(item.summary_status)} onClick={() => itemAction(item, "resummarize")} disabled={summarizeDisabled}>
                  <RefreshCcw size={16} />
                  {summarizeDisabled ? "生成中" : summarizeButtonLabel(item.summary_status)}
                </button>
              </div>
            </article>
          );
        })}
        {!isLoading && !items.length && !activeError && (
          <div className="empty">
            <Search size={22} /> No items yet. Subscribe to a source and run fetch.
          </div>
        )}
      </section>
    </div>
  );
}

function FeedSkeleton() {
  return (
    <>
      {Array.from({ length: 4 }, (_, index) => (
        <article className="item skeletonItem" aria-hidden="true" key={index}>
          <div className="skeletonTop">
            <span className="skeletonLine skeletonTitle" />
            <span className="skeletonLine skeletonStatus" />
          </div>
          <div className="skeletonMeta">
            <span className="skeletonLine skeletonSource" />
            <span className="skeletonLine skeletonPill" />
            <span className="skeletonLine skeletonPill" />
            <span className="skeletonLine skeletonDate" />
          </div>
          <div className="skeletonBody">
            <span className="skeletonLine" />
            <span className="skeletonLine skeletonWide" />
            <span className="skeletonLine skeletonShort" />
          </div>
          <div className="skeletonMeta">
            <span className="skeletonLine skeletonTag" />
            <span className="skeletonLine skeletonTag" />
            <span className="skeletonLine skeletonAction" />
          </div>
        </article>
      ))}
    </>
  );
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function FeedPage() {
  return (
    <Suspense fallback={<div className="empty">Loading feed...</div>}>
      <FeedView />
    </Suspense>
  );
}
