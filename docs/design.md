# Daily Info 全栈重构设计

## 1. 设计目标

本设计文档是 Daily Info 重构后的架构唯一真源。目标是把当前原型重构为一个易扩展、易部署、可长期运行的 self-hosted research reading desk。

设计优先级如下：

1. 新增信息源低摩擦：标准 RSS/Atom 和 RSSHub source 应通过配置和 UI 完成。
2. 部署低门槛：默认 Docker Compose + SQLite volume，Postgres、AI provider、自部署 RSSHub 都是可选增强。
3. 长期可运行：抓取、全文、摘要、聚类以 job 方式解耦，失败可观察、可重试。
4. 架构不过度拆分：采用单体 API + worker + scheduler，而不是微服务。

从本次重构开始，旧 `docs/superpowers` 下的 specs/plans 不再作为实现依据。

## 2. 总体拓扑

默认部署拓扑：

```text
browser
  -> web
  -> api
      -> SQLite volume
      -> worker
      -> scheduler
      -> external RSS/RSSHub/API targets
```

可选增强拓扑：

```text
browser
  -> web
  -> api
      -> Postgres
      -> worker
      -> scheduler
      -> rsshub profile
      -> OpenAI-compatible provider
      -> Codex CLI provider
```

服务职责：

- `web`：Next.js 前端。
- `api`：FastAPI HTTP API，负责读写 source、item、settings、health 和手动触发任务。
- `worker`：执行 fetch、parse、fulltext、summary、cluster 等后台 job。
- `scheduler`：根据 source 的 `poll_interval` 投递 fetch jobs。
- `rsshub`：可选自部署服务；系统也支持公共 RSSHub 实例和用户自定义实例。
- `storage`：默认 SQLite，可通过配置切换 Postgres。

API、worker、scheduler 可以使用同一后端镜像，通过不同 command 启动。

## 3. 前端信息架构

前端最小页面：

- `/`：Unified Feed。
- `/sources`：Source Registry 管理。
- `/sources/new`：新增 source。
- `/health`：运行状态和故障排查。
- `/settings`：运行环境概览、AI provider 配置和用量状态。

新增 source 流程：

```text
输入 URL 或 RSSHub route
  -> 系统识别 source 类型
  -> preview 最近 3 到 5 条内容
  -> 展示解析字段、正文可用性和警告
  -> 用户保存
  -> 用户启用
  -> scheduler 后续自动抓取
```

Feed 页面要求：

- 统一展示 paper、blog、post。
- URL query 保存筛选状态。
- 所有 AI 状态都显式展示，避免用户误以为摘要失败就是抓取失败。
- item 操作包括已读、收藏、隐藏、重新摘要和原文跳转。

## 4. 后端 Pipeline

后端处理链路：

```text
Source Registry
  -> scheduler
  -> fetch job
  -> SourceAdapter
  -> RawEntry
  -> normalize
  -> dedupe
  -> Item
  -> FulltextExtractor
  -> summary job
  -> cluster job
  -> API query
```

关键规则：

- item 入库不等待全文、摘要或聚类完成。
- 抓取失败不影响历史数据浏览。
- 全文失败不影响摘要队列创建，但摘要应知道正文是否可用。
- 摘要失败不影响阅读流。
- 每一步都写入 job 状态和错误信息。

## 5. Source Registry

DB 是运行时真源。YAML source pack 是默认来源、导入、导出、备份和分享的统一配置格式；默认源和用户源使用同一个 `SourceIn` 结构。

核心实体：

- `sources`
- `source_attempts`
- `source_runs`

`sources` 保存用户可编辑配置，例如名称、类型、平台、分组、轮询间隔、过滤词、默认标签、启停状态。默认来源从 `config/source-packs/default.yaml` 同步入库，入库后仍由 DB 承担运行时真源。

`source_attempts` 保存有序抓取尝试。每个 attempt 至少包含：

- `kind`: `direct`、`rsshub`、`html`、`manual`
- `adapter`: `feed`、`rsshub`、`html_index`、`manual`
- `url` 或 `route`
- `priority`
- `enabled`

first success wins。一个 attempt 成功的标准是：网络请求成功、返回内容可解析、解析后至少得到 1 条 raw entry。

## 6. Source 扩展模型

新增来源应优先配置化。只有当通用能力不足时才写代码。

### 6.1 SourceAdapter

`SourceAdapter` 负责把一个 attempt 转为 raw entries。

内置 adapter：

- `feed`：标准 RSS/Atom。
- `rsshub`：RSSHub route，本质输出 feed entries。
- `html_index`：少量没有 feed 的网页列表页。
- `manual`：后续支持手动导入 URL 或文本。

统一输出 `RawEntry`，至少包含 title、url、published_at、authors、summary、raw_payload。

### 6.2 FulltextExtractor

`FulltextExtractor` 负责把 item 或 raw entry 转为正文。

内置 extractor：

- `feed_field`：从 RSS `content`、`summary`、`description` 中取正文。
- `generic_article`：从 HTML `article`、`main` 等区域抽取正文。
- `site_specific`：OpenAI、Anthropic、Google 等特殊站点。

普通源应通过配置选择 extractor；特殊站点才新增 extractor 代码和 fixture 测试。

## 7. RSSHub 策略

RSSHub 不是唯一入口，而是 source discovery 和 post 扩展的重要能力。

Discovery 顺序：

1. 官方 RSS/Atom。
2. 已知 RSSHub route。
3. 公共 RSSHub 实例。
4. 用户自部署 RSSHub。
5. HTML/manual fallback。

系统配置：

- `RSSHUB_PUBLIC_INSTANCES`：公共实例列表。
- `RSSHUB_SELF_HOSTED_BASE_URL`：用户自部署实例。
- Docker Compose 提供 `rsshub` profile。

当 source 使用 RSSHub route 时，worker 按实例顺序尝试，不并发轰炸公共实例。成功后记录 `used_rsshub_instance`。

## 8. 数据模型

MVP 数据表：

- `sources`
- `source_attempts`
- `source_runs`
- `raw_entries`
- `items`
- `fulltexts`
- `jobs`
- `summaries`
- `clusters`
- `cluster_items`
- `settings`
- `llm_providers`

重要边界：

- `raw_entries` 保存原始解析结果，便于重跑 normalize 和 extractor。
- `items` 保存统一内容模型，供前端查询。
- `fulltexts` 保存正文抽取结果和 extractor 信息。
- `summaries` 保存结构化摘要 JSON、provider、model、prompt version、content hash。
- `jobs` 保存后台任务状态、重试次数和错误。

SQLite 和 Postgres 使用同一 repository 抽象。默认 SQLite 开启 WAL。Postgres 作为可选 profile，不是 MVP 硬依赖。

## 9. Job 状态机

所有慢操作都通过 job 执行。

Job 类型：

- `fetch_source`
- `summarize_item`

Source preview、source pack import/export 当前是请求内 API 操作；fulltext extraction 在 fetch pipeline 内执行。后续如果耗时或失败隔离需求变强，可以再提升为独立 job。

状态：

- `queued`
- `running`
- `succeeded`
- `failed`
- `retrying`
- `skipped`

每个 job 记录：

- `id`
- `type`
- `status`
- `payload`
- `attempts`
- `max_attempts`
- `scheduled_at`
- `started_at`
- `finished_at`
- `error_code`
- `error_message`

worker 可以先采用 DB-backed queue。后续如需扩展，可替换为 Redis/RQ/Celery，但 API 与业务状态不应依赖具体队列实现。

## 10. AI Provider 架构

AI 摘要通过统一 `LLMProvider` 接口接入。

```text
SummaryJob
  -> PromptBuilder
  -> LLMProvider
  -> SummaryValidator
  -> summaries table
```

内置 provider：

- `openai_compatible`
- `codex_cli`

Provider 配置来源：

- 环境变量。
- `/settings` UI。
- DB `llm_providers` 或 `settings` 表。

secret 不进入 source pack，不进入日志，不出现在 health 明文中。

### 10.1 OpenAI-compatible Provider

配置字段：

- `base_url`
- `api_key`
- `model_name`
- `temperature`
- `timeout`

该 provider 支持用户填写任意兼容 OpenAI 风格接口的服务地址和模型名。

### 10.2 Codex CLI Provider

`codex_cli` provider 由 worker 调用本机或容器内可用的 Codex CLI 接口生成摘要。

要求：

- settings 中可选择 provider type 为 `codex_cli`。
- health 中展示 Codex CLI 是否可用。
- 失败时记录命令不可用、认证失败、超时或输出解析失败等错误。
- 不把 Codex CLI 作为默认部署硬依赖。

## 11. 摘要与翻译

摘要 job 输入：

- item 基础字段。
- raw entry 摘要。
- fulltext 正文，若可用。
- source metadata。
- content type。

摘要 job 输出结构化 JSON。模板按 content type 区分：

- paper：研究问题、方法、结果、局限、意义。
- blog：发生了什么、要点、适合谁读、注意事项、意义。
- post：核心观点、上下文、信号类型、主观性提示、意义。

摘要状态：

- `not_configured`
- `pending`
- `ready`
- `failed`
- `skipped`

没有 AI provider 时，item 的摘要状态应为 `not_configured` 或 `skipped`，而不是错误。

## 12. 聚类

聚类先使用可解释规则，不依赖向量检索作为 MVP 前提。

初始信号：

- 相同 canonical URL。
- 相同 arXiv id。
- blog/post 引用同一论文链接。
- 标题高相似度。
- 共享高置信实体，例如模型名、项目名、论文名。

每个 cluster edge 记录原因，前端可解释为什么这些内容被放在一起。

## 13. API 面

MVP REST API：

- `GET /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/read`
- `POST /api/items/{id}/star`
- `POST /api/items/{id}/hide`
- `POST /api/items/{id}/resummarize`
- `GET /api/sources`
- `POST /api/sources`
- `PATCH /api/sources/{id}`
- `POST /api/sources/preview`
- `POST /api/sources/import`
- `GET /api/sources/export`
- `GET /api/health`
- `GET /api/settings`
- `PATCH /api/settings`
- `GET /api/clusters`

GraphQL 不进入 MVP。

## 14. 部署设计

默认 `.env` 最小配置：

```env
DATABASE_URL=sqlite:////data/daily-info.db
PUBLIC_APP_URL=http://localhost:3000
API_BASE_URL=http://api:8000
RSSHUB_PUBLIC_INSTANCES=https://rsshub.rssforever.com,https://rsshub.ktachibana.party,https://rsshub.cups.moe,https://rsshub-balancer.virworks.moe
```

可选 AI 配置：

```env
LLM_PROVIDER_TYPE=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL_NAME=
LLM_TEMPERATURE=0.2
```

可选 Codex CLI：

```env
LLM_PROVIDER_TYPE=codex_cli
CODEX_CLI_PATH=codex
CODEX_CLI_MODEL=
```

可选自部署 RSSHub：

```env
RSSHUB_SELF_HOSTED_BASE_URL=http://rsshub:1200
```

Compose profiles：

- 默认：web、api、worker、scheduler、SQLite volume。
- `rsshub`：启动自部署 RSSHub。
- `postgres`：启动 Postgres 并切换 DATABASE_URL。

## 15. 可观测性

`/health` 至少展示：

- latest run。
- 每个 source 最近成功时间。
- 近 24 小时 item 数。
- 连续失败次数。
- 连续空结果次数。
- 当前 degraded sources。
- fulltext 成功率。
- summary 成功率和失败率。
- 当前 AI provider 状态。
- 最近抓取错误。
- 最近摘要错误。

Health 页面必须区分抓取失败、全文失败、摘要失败和配置缺失。

## 16. 迁移与兼容

本次重构不要求兼容旧 artifacts。

迁移策略：

- 当前 `config/source-packs/default.yaml` 是初始默认 source pack。
- 旧 PRD 和旧 docs 已不再作为真源。
- 当前业务代码可逐步替换，但新实现的判断标准以 `PRD.md` 和 `docs/design.md` 为准。

后续实现应先完成部署骨架、DB-backed Source Registry 和 adapter/extractor 边界，再实现 AI 摘要和聚类。
