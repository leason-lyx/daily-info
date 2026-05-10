import { Item, Source } from "@/lib/api";

export const NO_SOURCE_SENTINEL = "__none__";

export const CONTENT_TYPE_LABELS: Record<string, string> = {
  paper: "Paper",
  blog: "Blog",
  post: "Post",
};

export const SOURCE_GROUP_ORDER = ["Papers", "Model Labs", "Engineering Blogs", "AI News", "Tech Media", "Post", "General"];

export const PRIORITY_OPTIONS = [
  { value: "p0", label: "P0 核心" },
  { value: "p1", label: "P1 重要" },
  { value: "p2", label: "P2 普通" },
  { value: "p3", label: "P3 低频" },
];

export function statusClass(status: string) {
  if (status === "ready") return "badge good";
  if (status === "failed") return "badge bad";
  if (status === "pending") return "badge warn";
  return "badge";
}

export function summaryStatusLabel(status: string) {
  if (status === "not_configured") return "AI summary off";
  if (status === "pending") return "Summarizing";
  if (status === "ready") return "AI summary ready";
  if (status === "failed") return "Summary failed";
  if (status === "skipped") return "Summary skipped";
  return status.replaceAll("_", " ");
}

export function summaryStatusTitle(status: string) {
  if (status === "not_configured") return "AI summary provider is not configured.";
  if (status === "pending") return "This item is waiting for AI summarization.";
  if (status === "ready") return "AI summary is available for this item.";
  if (status === "failed") return "AI summarization failed for this item.";
  if (status === "skipped") return "AI summarization was skipped for this item.";
  return "AI summary status.";
}

export function formatPublishedAt(value: string | null) {
  if (!value) return "No date";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function visibleAuthors(authors: string[]) {
  const normalized = authors.flatMap((author) => author.split(/\s*,\s*/)).map((author) => author.trim()).filter(Boolean);
  const shown = normalized.slice(0, 3);
  const hiddenCount = Math.max(normalized.length - shown.length, 0);
  return { shown, hiddenCount };
}

export function summaryText(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (value) return [String(value)];
  return [];
}

export function aiSummaryRows(item: Item) {
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

export function summarizeButtonLabel(status: string) {
  if (status === "pending") return "生成中";
  if (status === "ready") return "重新生成摘要";
  return "生成中文摘要";
}

export function readStatusLabel(read: boolean) {
  return read ? "已读" : "未读";
}

export function readButtonLabel(read: boolean) {
  return read ? "标为未读" : "标为已读";
}

export function fallbackSnippet(item: Item) {
  const text = item.summary || item.raw_text || "";
  return text ? `${text.slice(0, 360)}${text.length > 360 ? "..." : ""}` : "No summary text available yet.";
}

export function itemSourceRows(item: Item) {
  const rows = item.sources?.length ? item.sources : [{ source_id: item.source_id, source_name: item.source_name, url: item.url, tags: item.tags }];
  return rows.filter((source) => source.source_id || source.source_name);
}

export function itemSourceTitle(item: Item) {
  const rows = itemSourceRows(item);
  if (rows.length <= 1) return item.source_name;
  return rows.map((source) => source.source_name || source.source_id).join("\n");
}

export function selectedIdsFromParams(sourceRows: Source[], sourceParams: string[]) {
  if (sourceParams.includes(NO_SOURCE_SENTINEL)) return new Set<string>();
  if (sourceParams.length) return new Set(sourceParams);
  return new Set(sourceRows.map((source) => source.id));
}

export function sourceIdsForItemsQuery(sourceRows: Source[], sourceParams: string[]) {
  if (sourceParams.includes(NO_SOURCE_SENTINEL)) return [];
  if (sourceParams.length) return sourceRows.map((source) => source.id).filter((id) => sourceParams.includes(id));
  return sourceRows.map((source) => source.id);
}

export function sourceGroupName(source: Source) {
  return source.group || CONTENT_TYPE_LABELS[source.content_type] || "General";
}

export function sourceGroupRank(groupName: string) {
  const index = SOURCE_GROUP_ORDER.indexOf(groupName);
  return index === -1 ? SOURCE_GROUP_ORDER.length : index;
}

export function priorityLabel(source: Source) {
  const tier = source.priority_tier || tierFromPriority(source.effective_priority ?? source.priority ?? 100);
  const match = PRIORITY_OPTIONS.find((option) => option.value === tier);
  return match ? match.label : "P2 普通";
}

export function tierFromPriority(value: number) {
  if (value <= 24) return "p0";
  if (value <= 74) return "p1";
  if (value <= 124) return "p2";
  return "p3";
}

export function stringList(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  return value ? [String(value)] : [];
}

export function hasOwnValue(record: Record<string, unknown>, key: string) {
  return Object.prototype.hasOwnProperty.call(record, key);
}

export function itemQueryFromFilters(searchParams: URLSearchParams, sourceRows: Source[]) {
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

