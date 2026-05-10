"use client";

import { Suspense } from "react";
import { useEffect, useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Ban, Check, ExternalLink, Eye, EyeOff, RefreshCcw, Save, Search, Sparkles, Star, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import { feedApi, itemsApi, sourcesApi } from "@/lib/apiDomains";
import type { FeedPreset, Item, Source } from "@/lib/api";
import {
  NO_SOURCE_SENTINEL,
  PRIORITY_OPTIONS,
  aiSummaryRows,
  fallbackSnippet,
  formatPublishedAt,
  hasOwnValue,
  itemQueryFromFilters,
  itemSourceRows,
  itemSourceTitle,
  priorityLabel,
  readButtonLabel,
  readStatusLabel,
  selectedIdsFromParams,
  sourceGroupName,
  sourceGroupRank,
  statusClass,
  stringList,
  summarizeButtonLabel,
  summaryStatusLabel,
  summaryStatusTitle,
  visibleAuthors,
} from "@/features/feed/feedModel";

function FeedView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [sources, setSources] = useState<Source[]>([]);
  const [presets, setPresets] = useState<FeedPreset[]>([]);
  const [error, setError] = useState("");
  const [presetMessage, setPresetMessage] = useState("");
  const [savingPreset, setSavingPreset] = useState(false);
  const [newPresetName, setNewPresetName] = useState("");
  const [loadedQueryKey, setLoadedQueryKey] = useState<string | null>(null);
  const [failedQueryKey, setFailedQueryKey] = useState<string | null>(null);
  const [summarizingIds, setSummarizingIds] = useState<Set<string>>(new Set());
  const [feedbackPendingIds, setFeedbackPendingIds] = useState<Set<string>>(new Set());
  const [isPending, startTransition] = useTransition();
  const queryKey = searchParams.toString();
  const query = useMemo(() => new URLSearchParams(queryKey), [queryKey]);
  const isLoading = loadedQueryKey !== queryKey && failedQueryKey !== queryKey;
  const activeError = failedQueryKey === queryKey ? error : "";
  const activePresetId = searchParams.get("preset_id") || "all";
  const activePreset = presets.find((preset) => preset.id === activePresetId);
  const activePresetFilter = useMemo(() => activePreset?.filter || {}, [activePreset]);
  const searchQuery = searchParams.has("q") ? searchParams.get("q") || "" : String(activePresetFilter.q || "");
  const explicitSourceKey = searchParams.getAll("source_id").join("\u0000");
  const explicitGroupKey = searchParams.getAll("source_group").join("\u0000");
  const sourceParams = useMemo(() => {
    const explicitSourceParams = explicitSourceKey ? explicitSourceKey.split("\u0000") : [];
    if (explicitSourceParams.length) return explicitSourceParams;
    if (searchParams.has("source_group")) {
      const explicitGroups = explicitGroupKey ? explicitGroupKey.split("\u0000").filter(Boolean) : [];
      if (!explicitGroups.length) return [];
      const groupSet = new Set(explicitGroups);
      return sources.filter((source) => groupSet.has(sourceGroupName(source))).map((source) => source.id);
    }
    const presetSourceIds = stringList(activePresetFilter.source_ids || activePresetFilter.source_id);
    if (presetSourceIds.length) return presetSourceIds;
    const presetGroups = new Set(stringList(activePresetFilter.groups || activePresetFilter.source_group));
    if (presetGroups.size) return sources.filter((source) => presetGroups.has(sourceGroupName(source))).map((source) => source.id);
    return [];
  }, [activePresetFilter, explicitGroupKey, explicitSourceKey, searchParams, sources]);
  const currentSince = searchParams.has("since") ? searchParams.get("since") || "" : String(activePresetFilter.since || "");
  const currentPriorityTier = searchParams.has("priority_tier")
    ? searchParams.get("priority_tier") || ""
    : stringList(activePresetFilter.priority_tiers || activePresetFilter.priority_tier)[0] || "";
  const currentSummaryStatus = searchParams.has("summary_status") ? searchParams.get("summary_status") || "" : String(activePresetFilter.summary_status || "");
  const presetRank = activePreset?.rank?.mode === "recommended" || activePreset?.rank?.mode === "for_you" ? String(activePreset.rank.mode) : "";
  const currentRank = searchParams.has("rank") ? searchParams.get("rank") || "" : presetRank;
  const presetAdjusted = Boolean(searchParams.get("preset_id")) && ["source_id", "source_group", "priority_tier", "since", "q", "summary_status", "rank"].some((key) => searchParams.has(key));
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
        sources: group.sources.sort((a, b) => a.priority - b.priority || a.title.localeCompare(b.title)),
      }))
      .sort((a, b) => {
        return sourceGroupRank(a.groupName) - sourceGroupRank(b.groupName) || a.groupName.localeCompare(b.groupName);
      });
  }, [sources]);

  useEffect(() => {
    let alive = true;

    async function loadFeed() {
      try {
        const [sourceRows, presetRows] = await Promise.all([sourcesApi.getSources(), feedApi.getFeedPresets()]);
        if (!alive) return;
        setPresets(presetRows);
        const subscribedRows = sourceRows.filter((source) => source.subscribed);
        setSources(subscribedRows);
        const feed = await feedApi.getItems(itemQueryFromFilters(query, subscribedRows));
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
    const presetHasDefault =
      key === "rank"
        ? activePreset?.rank?.mode && activePreset.rank.mode !== "latest"
        : key === "priority_tier"
          ? Boolean(activePresetFilter.priority_tiers || activePresetFilter.priority_tier || activePresetFilter.priority_min || activePresetFilter.priority_max)
          : hasOwnValue(activePresetFilter, key);
    if (value || (searchParams.has("preset_id") && presetHasDefault)) next.set(key, value);
    else next.delete(key);
    replaceQuery(next);
  }

  function selectPreset(presetId: string) {
    const next = new URLSearchParams();
    next.set("preset_id", presetId);
    replaceQuery(next);
  }

  function currentViewFilter() {
    const filter: Record<string, unknown> = { ...activePresetFilter };
    const sourceParamsForSave = searchParams.getAll("source_id");
    const sourceIds = sourceParamsForSave.filter((sourceId) => sourceId !== NO_SOURCE_SENTINEL);
    const priorityTiers = searchParams.getAll("priority_tier");
    if (sourceParamsForSave.includes(NO_SOURCE_SENTINEL)) {
      filter.source_ids = [NO_SOURCE_SENTINEL];
      delete filter.groups;
      delete filter.source_group;
    } else if (sourceIds.length) {
      filter.source_ids = sourceIds;
      delete filter.groups;
      delete filter.source_group;
    }
    if (searchParams.has("priority_tier")) {
      const values = priorityTiers.filter(Boolean);
      if (values.length) filter.priority_tiers = values;
      else {
        delete filter.priority_tiers;
        delete filter.priority_tier;
        delete filter.priority_min;
        delete filter.priority_max;
      }
    }
    for (const key of ["q", "since", "summary_status"]) {
      if (!searchParams.has(key)) continue;
      const value = searchParams.get(key) || "";
      if (value) filter[key] = value;
      else delete filter[key];
    }
    return filter;
  }

  async function saveCurrentPreset() {
    const name = newPresetName.trim();
    if (!name) return;
    setSavingPreset(true);
    setPresetMessage("");
    try {
      const saved = await feedApi.createFeedPreset({
        name,
        description: "",
        filter: currentViewFilter(),
        rank: { mode: currentRank || "latest" },
        sort_order: 100 + presets.filter((preset) => !preset.is_builtin).length,
      });
      setPresets((rows) => [...rows, saved].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name)));
      setNewPresetName("");
      selectPreset(saved.id);
      setPresetMessage(`已保存 ${saved.name}`);
    } catch (err) {
      setPresetMessage(err instanceof Error ? err.message : "保存预设失败");
    } finally {
      setSavingPreset(false);
    }
  }

  async function deletePreset(preset: FeedPreset) {
    if (preset.is_builtin) return;
    setPresetMessage("");
    try {
      await feedApi.deleteFeedPreset(preset.id);
      setPresets((rows) => rows.filter((row) => row.id !== preset.id));
      if (activePresetId === preset.id) selectPreset("all");
    } catch (err) {
      setPresetMessage(err instanceof Error ? err.message : "删除预设失败");
    }
  }

  function setSourceFilter(nextSourceIds: string[]) {
    const next = new URLSearchParams(searchParams.toString());
    next.delete("source_id");
    if (activePresetFilter.groups || activePresetFilter.source_group || searchParams.has("source_group")) {
      next.set("source_group", "");
    }
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
    const updated = action === "resummarize" ? await itemsApi.resummarize(item.id) : await itemsApi.markItem(item.id, action);
    setItems((rows) => rows.map((row) => (row.id === item.id ? updated : row)));
    if (action === "resummarize" && updated.summary_status === "pending") {
      pollItemSummary(item.id);
    }
  }

  async function feedbackAction(item: Item, eventType: "more_like_this" | "less_like_this" | "dismiss") {
    setFeedbackPendingIds((ids) => new Set(ids).add(item.id));
    setPresetMessage("");
    const previousItems = items;
    const previousTotal = total;
    try {
      if (eventType === "dismiss") {
        setItems((rows) => rows.filter((row) => row.id !== item.id));
        setTotal((value) => Math.max(value - 1, 0));
      }
      await itemsApi.recordItemEvent(item.id, eventType, { preset_id: activePresetId, rank: currentRank || "latest" });
      setPresetMessage(eventType === "more_like_this" ? "已记录：更多类似内容" : eventType === "less_like_this" ? "已记录：减少类似内容" : "已记录：不感兴趣");
    } catch (err) {
      if (eventType === "dismiss") {
        setItems(previousItems);
        setTotal(previousTotal);
      }
      setPresetMessage(err instanceof Error ? err.message : "记录反馈失败");
    } finally {
      setFeedbackPendingIds((ids) => {
        const next = new Set(ids);
        next.delete(item.id);
        return next;
      });
    }
  }

  async function pollItemSummary(itemId: string) {
    setSummarizingIds((ids) => new Set(ids).add(itemId));
    try {
      for (let i = 0; i < 20; i += 1) {
        await delay(2000);
        const updated = await itemsApi.getItem(itemId);
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

      <section className="presetBar" aria-label="Feed presets">
        <div className="presetButtons">
          {presets.map((preset) => {
            const active = activePresetId === preset.id || (!searchParams.get("preset_id") && preset.id === "all");
            return (
              <span className="presetControl" key={preset.id}>
                <button
                  type="button"
                  className={active ? "presetButton active" : "presetButton"}
                  title={preset.description || preset.name}
                  onClick={() => selectPreset(preset.id)}
                >
                  {preset.name}
                  {active && presetAdjusted && <span className="presetAdjusted">已调整</span>}
                </button>
                {!preset.is_builtin && (
                  <button className="presetDelete" type="button" title={`删除 ${preset.name}`} aria-label={`删除 ${preset.name}`} onClick={() => void deletePreset(preset)}>
                    <Trash2 size={14} />
                  </button>
                )}
              </span>
            );
          })}
        </div>
        <div className="presetSave">
          <input
            value={newPresetName}
            onChange={(event) => setNewPresetName(event.target.value)}
            placeholder="自定义预设名称"
            aria-label="自定义预设名称"
          />
          <button className="button compact" type="button" onClick={() => void saveCurrentPreset()} disabled={!newPresetName.trim() || savingPreset}>
            <Save size={16} />
            {savingPreset ? "保存中" : "保存视图"}
          </button>
        </div>
        {presetMessage && <span className="subtle">{presetMessage}</span>}
        {activePreset && <span className="subtle">{activePreset.description}</span>}
      </section>

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
            <select id="feed-window" value={currentSince} onChange={(e) => setParam("since", e.target.value)}>
              <option value="">Any time</option>
              <option value="today">Today</option>
              <option value="3d">Past 3 days</option>
              <option value="7d">Past 7 days</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="feed-priority">Priority</label>
            <select id="feed-priority" value={currentPriorityTier} onChange={(e) => setParam("priority_tier", e.target.value)}>
              <option value="">Any</option>
              {PRIORITY_OPTIONS.map((option) => (
                <option value={option.value} key={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="feed-summary-status">Summary</label>
            <select id="feed-summary-status" value={currentSummaryStatus} onChange={(e) => setParam("summary_status", e.target.value)}>
              <option value="">Any</option>
              <option value="not_configured">AI summary off</option>
              <option value="pending">Summarizing</option>
              <option value="ready">AI summary ready</option>
              <option value="failed">Summary failed</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="feed-rank">Rank</label>
            <select id="feed-rank" value={currentRank} onChange={(e) => setParam("rank", e.target.value)}>
              <option value="">Latest</option>
              <option value="for_you">For You</option>
              <option value="recommended">Recommended</option>
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
                            <span>{source.title}</span>
                            <span>{priorityLabel(source)} · {source.platform || source.group || source.id}</span>
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
          const feedbackPending = feedbackPendingIds.has(item.id);

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
                  <span className="sourceLinks" title={itemSourceTitle(item)}>
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
              {item.recommendation_reasons?.length ? (
                <div className="recommendationMeta">
                  <span className="badge good">Score {Math.round((item.recommendation_score || 0) * 10) / 10}</span>
                  {item.recommendation_reasons.map((reason) => (
                    <span className="badge" key={reason}>{reason}</span>
                  ))}
                </div>
              ) : null}
              <div className="actions">
                <a className="button" href={item.url} target="_blank" rel="noreferrer" onClick={() => void itemsApi.recordItemEvent(item.id, "open", { url: item.url })}>
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
                {currentRank === "for_you" ? (
                  <>
                    <button className="button" type="button" title="推荐更多类似内容" onClick={() => void feedbackAction(item, "more_like_this")} disabled={feedbackPending}>
                      <ThumbsUp size={16} /> 更多类似
                    </button>
                    <button className="button" type="button" title="减少类似内容" onClick={() => void feedbackAction(item, "less_like_this")} disabled={feedbackPending}>
                      <ThumbsDown size={16} /> 减少类似
                    </button>
                    <button className="button" type="button" title="从本次推荐中移除" onClick={() => void feedbackAction(item, "dismiss")} disabled={feedbackPending}>
                      <Ban size={16} /> 不感兴趣
                    </button>
                  </>
                ) : null}
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
