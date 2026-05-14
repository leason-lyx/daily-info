import { describe, expect, it } from "vitest";
import { item, source } from "@/test/factories";
import {
  NO_SOURCE_SENTINEL,
  aiSummaryRows,
  fallbackSnippet,
  itemQueryFromFilters,
  itemSourceRows,
  itemSourceTitle,
  priorityLabel,
  selectedIdsFromParams,
  sourceGroupName,
  sourceGroupRank,
  sourceIdsForItemsQuery,
  summarizeButtonLabel,
  summaryStatusLabel,
  summaryStatusTitle,
  visibleAuthors,
} from "./feedModel";

describe("feedModel", () => {
  const paperSource = source({ id: "paper", title: "Paper Source", group: "Papers", priority: 10, effective_priority: 10, priority_tier: "p0" });
  const blogSource = source({ id: "blog", title: "Blog Source", kind: "blog", group: "Engineering Blogs", priority: 90, effective_priority: 90, priority_tier: "p2" });

  it("selects all sources by default and honors explicit source params", () => {
    expect(selectedIdsFromParams([paperSource, blogSource], [])).toEqual(new Set(["paper", "blog"]));
    expect(selectedIdsFromParams([paperSource, blogSource], ["blog"])).toEqual(new Set(["blog"]));
    expect(selectedIdsFromParams([paperSource, blogSource], [NO_SOURCE_SENTINEL])).toEqual(new Set());
  });

  it("builds item query source filters without leaking UI-only params", () => {
    const allQuery = itemQueryFromFilters(new URLSearchParams("hidden=1&q=agent"), [paperSource, blogSource]);
    expect(allQuery.toString()).toBe("q=agent");

    const partialQuery = itemQueryFromFilters(new URLSearchParams("source_id=blog&hidden=1"), [paperSource, blogSource]);
    expect(partialQuery.getAll("source_id")).toEqual(["blog"]);

    const noneQuery = itemQueryFromFilters(new URLSearchParams(`source_id=${NO_SOURCE_SENTINEL}`), [paperSource, blogSource]);
    expect(noneQuery.getAll("source_id")).toEqual([NO_SOURCE_SENTINEL]);
    expect(sourceIdsForItemsQuery([paperSource, blogSource], ["missing"])).toEqual([]);
  });

  it("maps source grouping and priority labels", () => {
    expect(sourceGroupName(source({ group: "" }))).toBe("Paper");
    expect(sourceGroupRank("Papers")).toBeLessThan(sourceGroupRank("General"));
    expect(sourceGroupRank("Unknown")).toBeGreaterThan(sourceGroupRank("General"));
    expect(priorityLabel(source({ priority_tier: "", effective_priority: 10 }))).toBe("P0 核心");
    expect(priorityLabel(source({ priority_tier: "", effective_priority: 140 }))).toBe("P3 低频");
  });

  it("formats author lists, fallback text, summary labels, and AI rows", () => {
    expect(visibleAuthors(["Ada, Grace", "Alan", "Barbara"])).toEqual({ shown: ["Ada", "Grace", "Alan"], hiddenCount: 1 });
    expect(fallbackSnippet(item({ summary: "", raw_text: "x".repeat(361) }))).toHaveLength(363);
    expect(fallbackSnippet(item({ summary: "", raw_text: "" }))).toBe("No summary text available yet.");
    expect(summaryStatusLabel("not_configured")).toBe("AI summary off");
    expect(summaryStatusLabel("custom_state")).toBe("custom state");
    expect(summaryStatusTitle("failed")).toContain("failed");
    expect(summarizeButtonLabel("pending")).toBe("生成中");

    const rows = aiSummaryRows(item());
    expect(rows.map((row) => row.label)).toEqual(["研究问题", "方法", "关键结果"]);
  });

  it("derives item source display rows and titles", () => {
    const multiSourceItem = item({
      sources: [
        { source_id: "a", source_name: "A", url: "https://a.example", tags: [] },
        { source_id: "b", source_name: "B", url: "https://b.example", tags: [] },
      ],
    });

    expect(itemSourceRows(item({ sources: [] }))[0].source_id).toBe("arxiv-cs-se");
    expect(itemSourceTitle(multiSourceItem)).toBe("A\nB");
  });
});
