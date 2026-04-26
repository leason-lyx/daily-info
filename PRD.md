# Daily Info PRD

## 1. 产品定义

Daily Info 是一个面向 AI researcher 和工程师的 self-hosted research reading desk。它把 paper、blog、post 以及通过 RSSHub 转换出来的社交/频道内容放进统一信息流，帮助用户在一个地方完成每日研究情报浏览、筛选、收藏、摘要和回看。

产品的核心不是“尽可能多抓取全网内容”，而是让用户维护一组可信信息源，并把每天新增内容压缩成可以在 10 到 20 分钟内扫完的研究情报面板。

从本次重构开始，`PRD.md` 是产品需求的唯一真源；旧 PRD 和旧 `docs/superpowers` 设计文档不再作为实现依据。

## 2. 目标用户

主要用户包括：

- 每天关注 arXiv、模型厂商 blog、研究者动态和工程博客的 AI researcher。
- 希望把技术信息源沉淀成长期资产的 AI/LLM 工程师。
- 愿意 self-host 开源工具、但不希望维护复杂基础设施的个人用户或小团队。

产品首先服务个人和小团队，不以企业多租户系统为目标。

## 3. 核心价值

Daily Info 要解决四个问题：

1. 把分散在 paper、blog、post、RSSHub route 中的信息统一成一个阅读入口。
2. 让新增 RSS/Atom、RSSHub route 或博客来源尽量配置化完成，特殊来源才需要写 adapter 或 extractor。
3. 让 GitHub 用户可以用 Docker Compose 快速部署，而不是先理解复杂数据库、队列和外部服务。
4. 在可选 AI 配置存在时，为每条内容生成中文标题和结构化摘要；没有 AI 配置时仍可作为可靠阅读器运行。

## 4. 非目标

MVP 不做以下能力：

- 多用户权限、组织协作和商业订阅。
- 移动端 App。
- 完整 PDF 解析和论文全文问答。
- 复杂推荐算法或个性化排序模型。
- 社交评论区。
- 激进反爬、验证码绕过或代理池。
- 默认依赖 Postgres、外部队列或云服务。

## 5. 内容范围

系统支持三类统一内容对象：

- `paper`：arXiv 分类流、查询流和后续论文源。
- `blog`：模型厂商 blog、研究博客、工程博客、release notes、技术公告。
- `post`：通过 RSSHub 或其他 adapter 转换出的社交账号、频道、论坛或短内容源。

前端只面向统一 `Item` 模型渲染；`paper`、`blog`、`post` 是筛选维度，不是三个独立产品。

## 6. 核心场景

### 6.1 首次部署

用户 clone 仓库后复制 `.env.example`，执行：

```bash
docker compose up -d
```

系统应在没有 AI key、没有 Postgres、没有额外云服务的情况下启动。默认使用 SQLite volume 保存数据。Postgres、外部 RSSHub、AI provider 都是可选增强。

### 6.2 启用默认来源

系统提供默认 source pack，包含常见 AI 信息源，例如 arXiv、OpenAI、Anthropic、Hugging Face 和中文 AI 早报等。默认来源和用户新增来源使用同一个 source 配置结构，区别只在来源和所有权。用户可以在 `/sources` 中一键启用、禁用或抓取。

### 6.3 新增 RSS/RSSHub 来源

用户在 `/sources/new` 输入官方 RSS/Atom URL、RSSHub route 或普通网页 URL。系统按以下顺序处理：

1. 优先识别官方 RSS/Atom。
2. 查找或使用已有 RSSHub route。
3. 使用用户配置的公共 RSSHub 实例或自部署 RSSHub。
4. 必要时进入 HTML/manual fallback。

保存前必须支持 preview 最近 3 到 5 条内容，展示标题、时间、链接、正文/摘要可用性和解析警告。

### 6.4 每日阅读

用户打开首页看到统一 feed，默认按发布时间展示最新内容。用户可按内容类型、来源、时间、标签、摘要状态、已读/收藏/隐藏状态筛选。

### 6.5 查看健康状态

用户在 `/health` 看到每个 source 的最近成功时间、连续失败次数、连续空结果次数、抓取数量、全文成功率、摘要失败率和最近错误。

### 6.6 配置 AI 摘要

用户可在 `/settings` 或环境变量中配置 AI provider。MVP 至少支持：

- `openai_compatible`：自定义 base URL、API key、model name、temperature。
- `codex_cli`：通过本机或容器内可用的 Codex CLI 接口生成摘要。

AI 配置不是最小部署硬依赖。未配置时，系统仍可抓取、阅读、搜索和管理来源。

## 7. Source Registry

Source Registry 是系统的核心资产。重构后 DB 是运行时真源，YAML source pack 是默认来源、导入、导出、备份和分享的统一配置格式。

每个 source 至少包含：

- `id`
- `name`
- `content_type`
- `platform`
- `homepage_url`
- `enabled`
- `group`
- `priority`
- `poll_interval`
- `language_hint`
- `include_keywords`
- `exclude_keywords`
- `default_tags`
- `attempts`
- `fulltext`
- `auth_mode`
- `stability_level`

每个 source 可以包含多个有序 `SourceAttempt`。系统按顺序尝试，first success wins。

Source 管理页必须支持：

- 从默认 source pack 启用来源。
- 新增用户 source。
- 在 `/sources/new` 保存前 preview。
- 编辑 source 名称、分组、轮询间隔、过滤词、默认标签、attempts 和全文策略。
- 启用/禁用 source。
- 导入和导出 YAML source pack。
- 查看健康状态和最近错误。

Source pack 不能包含 secret。API key、cookie、token 等敏感信息必须通过 settings 或环境变量配置。

## 8. RSSHub 策略

RSSHub 是扩展 post 和无官方 feed 来源的重要入口，但不是唯一入口。

系统策略如下：

1. 优先使用网站已有官方 RSS/Atom。
2. 如果没有官方 feed，优先查找已有 RSSHub route。
3. 支持配置公共 RSSHub 实例列表。
4. 支持用户自部署 RSSHub，并在 Docker Compose 中提供可选 profile。
5. 当 RSSHub route 不稳定时，支持 fallback 到其他 attempt。

系统不应默认假设所有来源都必须通过 RSSHub，也不应把 RSSHub route 逻辑写死在业务 pipeline 中。

## 9. 阅读流需求

首页统一 feed 至少支持：

- 按 `paper`、`blog`、`post` 筛选。
- 按 source、source group、platform 筛选。
- 按今天、过去 3 天、过去 7 天、自定义时间筛选。
- 按关键词搜索标题、摘要、正文、作者、来源名和标签。
- 按摘要状态筛选：`not_configured`、`pending`、`ready`、`failed`。
- 已读、收藏、隐藏。
- 原文跳转。
- 卡片展开/折叠。
- URL query 持久化筛选条件。

卡片默认展示：

- 中文标题优先，允许查看原始标题。
- 来源、类型、发布时间、作者/组织。
- 一句话摘要或原始摘要。
- 摘要状态。
- 标签和实体。
- 原文链接。
- 已读、收藏、隐藏、重新摘要操作。

## 10. AI 摘要需求

AI 摘要是可选增强能力。抓取和阅读不能依赖 AI 摘要成功。

系统需要支持三类结构化摘要模板。

### 10.1 Paper 模板

- `one_sentence`
- `research_question`
- `method`
- `key_results`
- `limitations`
- `why_it_matters`

### 10.2 Blog 模板

- `one_sentence`
- `what_happened`
- `key_takeaways`
- `who_should_read`
- `caveats`
- `why_it_matters`

### 10.3 Post 模板

- `one_sentence`
- `main_update_or_claim`
- `context`
- `signal_type`
- `subjectivity_notice`
- `why_it_matters`

摘要输出必须是结构化 JSON。系统应记录 provider、model、prompt version、content hash、状态、错误信息和创建时间。

## 11. AI Provider 配置

MVP 至少支持两类 provider。

### 11.1 OpenAI-compatible Provider

用户可配置：

- provider type
- base URL
- API key
- model name
- temperature
- timeout

该 provider 应兼容 OpenAI-style chat/completions 或 responses 接口的服务形态。具体请求实现可在后续实现计划中细化，但文档层面必须保留自定义 endpoint 和 model 的能力。

### 11.2 Codex CLI Provider

用户可选择 `codex_cli` provider。worker 通过本机或容器内可用的 Codex CLI 接口发起摘要任务。

Codex CLI provider 是可选项，不是默认部署硬依赖。系统需要在 health/settings 中展示 provider 是否可用，以及最近错误。

## 12. 部署需求

默认部署目标是个人单机 Docker Compose。

最小部署：

- `web`
- `api`
- `worker`
- `scheduler`
- SQLite data volume

可选增强：

- Postgres profile。
- 自部署 RSSHub profile。
- 外部 RSSHub instances。
- AI provider 配置。

`.env.example` 必须说明：

- 数据库配置。
- RSSHub 公共实例列表和自部署 base URL。
- OpenAI-compatible provider 配置。
- Codex CLI provider 配置。
- Web/API URL 配置。

## 13. MVP 验收标准

MVP 达标标准：

1. 新用户可在 10 分钟内通过 Docker Compose 启动系统。
2. 无 AI 配置时，系统可以启用默认源、抓取内容、浏览 feed、搜索和查看 health。
3. 用户可以通过 UI 新增标准 RSS/Atom source。
4. 用户可以通过 UI 新增 RSSHub route source，并使用公共或自部署 RSSHub。
5. Source preview 能在保存前显示最近 3 到 5 条解析结果。
6. Source health 能展示最近成功、连续失败、连续空结果和最近错误。
7. 配置 OpenAI-compatible provider 后，系统能异步生成结构化中文摘要。
8. 配置 Codex CLI provider 后，系统能通过该 provider 生成摘要或给出明确错误。
9. source pack 可以导入、导出，且不包含 secret。
10. 新文档和实现以 `PRD.md` 与 `docs/design.md` 为准。
