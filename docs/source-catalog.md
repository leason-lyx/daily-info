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

每个 YAML 文件包含一组 source definitions。应用启动时会同步这些定义到数据库。同步不会把未订阅 source 自动加入默认 feed。

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
- `rsshub`：RSSHub route 或 RSSHub URL。
- `html_index`：没有 feed 时的 HTML 列表页 fallback。

attempt 可以配置：

- `url`
- `route`
- `timeout_seconds`
- `selectors`
- `limit`

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

## Secret 规则

Source catalog 不能包含真实 secret 值。

禁止写入 catalog：

- API key
- cookie
- bearer token
- 私钥
- 个人账号凭据

如果未来某个 source 需要认证，catalog 中只保存 `secret_ref` 这样的引用名，真实 secret 放在 `.env`、settings 或其他运行时 secret store。

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
