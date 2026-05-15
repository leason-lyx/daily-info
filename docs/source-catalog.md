# Source Catalog

Source Catalog 是 Daily Info 的核心配置资产。它把“系统知道有哪些 source”和“用户订阅哪些 source”拆开管理。

## 基本模型

- Source definition：定义一个信息源的稳定元数据和抓取方式，来自 `config/sources/*.yaml` 或用户创建。
- Subscription：用户是否订阅某个 source。只有订阅 source 才会被 scheduler 抓取，并默认进入 feed。
- Runtime state：最近抓取时间、失败次数、空结果次数、最近错误等运行时信息。

## 内置目录

内置信息源位于：

```text
config/sources/
```

每个 YAML 文件包含一组只读内置 source definitions。应用启动时会把这些 seed 同步到数据库。同步不会把未订阅 source 自动加入默认 feed。

运行时 catalog 以数据库为准：`/sources` 页面编辑或新建 source 时，后端只更新数据库中的 definition，不会修改仓库里的 `config/sources/*.yaml`。内置 YAML 继续作为可审查、可版本管理的 seed；用户自定义和覆盖配置保存在数据库。本轮不保留旧 source-pack 导入/导出 API，需要版本化运行时修改时应人工审查数据库中的 definition，再整理成新的 `config/sources/*.yaml` 变更。

`Personal Posts` 分组用于单人更新源。内置个人源优先使用官方 RSS 或 Atom，并通过 `feed` adapter 抓取，避免默认 catalog 依赖私有 RSSHub 凭据。

## Source Definition 字段

常用字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定唯一标识。 |
| `title` | 展示名称。 |
| `kind` | `paper`、`blog` 或 `post`。 |
| `platform` | 平台或站点名。 |
| `homepage` | 官网或栏目页。 |
| `language` | 语言提示，如 `en`、`zh-CN`。 |
| `tags` | 默认标签。 |
| `tagging` | 单篇内容的标签策略。 |
| `group` | UI 分组。 |
| `priority` | 同组排序权重。 |
| `fetch` | 抓取策略和 attempts。 |
| `fulltext` | 全文抽取策略。 |
| `summary` | 自动摘要策略。 |
| `filters` | include/exclude 关键词。 |
| `auth` | 认证模式和 secret 引用。 |
| `stability` | source 稳定性标记。 |

## Fetch Attempts

一个 source 可以有多个有序 attempt，worker 按顺序尝试，first success wins。

支持的 adapter：

- `feed`：标准 RSS/Atom。
- `rsshub`：RSSHub route 或 RSSHub URL；适合无需私有账号凭据即可稳定访问的 RSSHub 路由。
- `html_index`：没有 feed 时的 HTML 列表页 fallback。
- `page_index`：官方列表页解析，提取文章链接和发布时间；适合 RSSHub route 漏项、上游无专用 feed，或类似 Alignment Science Blog 这种按年份路径发布的 source。

attempt 可以配置：

- `url`
- `route`
- `timeout_seconds`
- `selectors`
- `limit`
- `reader_fallback`：`page_index` 可用；当官方页面阻止普通 HTTP 抓取时，通过 reader fallback 获取页面文本。

## Fulltext Policy

支持模式：

- `feed_only`：只使用 feed 字段。
- `detail_only`：抓取详情页正文。
- `feed_then_detail`：feed 正文不足时再抓详情页。

常用参数：

- `min_feed_chars`
- `max_detail_pages_per_run`
- `selectors`
- `remove_selectors`
- `min_detail_chars`

网页编辑 v1 开放 `mode`、`min_feed_chars` 和 `max_detail_pages_per_run`。

## Tagging Policy

`tagging` 控制单篇 item 入库时如何生成最终标签：

- `mode: feed`：使用 RSS/Atom entry 自带的 category/tag，并合并 source 默认标签。适合 arXiv 这类标签稳定的 source。
- `mode: llm`：忽略 feed 标签，使用已配置的 AI provider 生成主题标签，并合并 source 默认标签。适合 feed 标签容易混入页面 class 的 source。
- `mode: default`：只使用 source 默认标签。

可选字段：

- `max_tags`：最终保留的最大标签数，默认 5。

网页编辑 v1 开放 `mode` 和 `max_tags`。

为避免单次抓取历史内容时产生大量同步 AI 请求，`llm` 模式每次 source 抓取最多会为 20 篇新内容生成标签；已有非默认标签的 item/source 关系会保留原标签，不会因为临时 AI 失败被降级为默认标签。标签生成的请求次数、错误与 token 会计入 AI 用量统计。

所有模式都会先清洗标签，去掉 `px-0`、`cols-12`、`span-6`、`start-4`、`mb-0` 这类布局/CSS 噪声。

## Secret 规则

Source catalog 不能包含真实 secret 值。

禁止写入 catalog：

- API key
- cookie
- bearer token
- 私钥
- 个人账号凭据

如果未来某个 source 需要认证，catalog 中只保存 `secret_ref` 这样的引用名，真实 secret 放在 `.env`、settings 或其他运行时 secret store。

需要私有凭据的 RSSHub route 不应作为默认内置源。如果以后加入这类 source，catalog 中只保存公开 route 和 `secret_ref`，真实 cookie/token 继续放在运行时环境中。

网页编辑也遵守同一规则：不要在 source 的标签、过滤词、URL、metadata 或 auth 字段里保存真实 secret。

## Feed 预设与优先级

Feed 首页支持用预设一键切换阅读视图。内置预设定义在 `config/feed-presets.yaml`，自定义预设保存在数据库。预设只保存筛选和排序偏好，不会自动订阅 source，也不会改变 catalog definition。

source 优先级使用“数字越小越重要”的规则：P0 为 0-24，P1 为 25-74，P2 为 75-124，P3 为 125 及以上。默认展示优先使用订阅上的 `priority_override`，没有覆盖值时使用 source definition 的 `priority`。因为 item 会跨 source 去重，feed 的优先级和 group/source 过滤都按 `item_sources` 判断：只要任一来源命中筛选，这条 item 就会进入候选集。

`rank=recommended` 保留轻量请求时规则排序；内置 `For You` 预设使用 `rank=for_you`，默认候选窗口是最近 30 天内容，重要源的未读内容可放宽到 90 天。`For You` 优先读取 `ItemRecommendationScore` 缓存，缓存缺失或过期时回退到同一套可解释规则。

`For You` 分数由显式推荐偏好、带时间衰减的行为事件、source 有效优先级、发布时间、内容质量、多来源站内热度和外部趋势信号组成。用户可以在 Settings 里编辑兴趣词、排除词、关注 source id、标签/实体/平台/内容类型和外部热度 provider。Feed 中的“更多类似 / 减少类似 / 不感兴趣”会写入 item event，用于后续画像和排序，并让相关推荐缓存过期。

推荐相关表包括 `user_preferences`、`item_embeddings`、`external_trend_signals`、`item_recommendation_scores` 和 `recommendation_runs`。第一阶段仍按单用户设计，统一使用 `profile_id="default"`；推荐只改变排序和解释，不会自动订阅 source，也不会改变去重后的来源归属。

## 新增 Source 建议

优先级：

1. 官方 RSS/Atom。
2. 稳定 RSSHub route。
3. HTML index fallback。
4. 只有通用能力不足时，再新增 adapter 或 extractor 代码。

新增或修改 source 后建议：

- 在 `/sources` 里 preview。
- 订阅后手动 fetch 一次。
- 到 `/health` 查看最近 run、错误和全文覆盖情况。
- 如果需要版本化运行时修改，人工审查数据库中的 definition 后整理成 `config/sources/*.yaml` 变更。
