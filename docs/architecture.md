# 架构说明

Daily Info 采用简单的单体后端加后台进程架构，避免把个人自托管工具拆成复杂微服务。

## 服务拓扑

默认 Docker Compose 栈包含四个服务：

- `web`：Next.js 前端，默认暴露 `http://localhost:3000`。
- `api`：FastAPI 后端，默认暴露 `http://localhost:8000`。
- `worker`：后台 job runner，执行抓取、全文抽取和摘要任务。
- `scheduler`：周期性扫描订阅源并投递 fetch jobs。

可选服务：

- `rsshub`：通过 Compose profile 启动的自托管 RSSHub 实例。
- `postgres`：可选数据库 profile；默认路径仍是 SQLite。

## 数据流

核心链路：

```text
Source Catalog
  -> Subscription
  -> Scheduler
  -> fetch_source job
  -> Adapter
  -> RawEntry
  -> Item
  -> Fulltext
  -> summarize_item job
  -> API
  -> Web
```

关键规则：

- `config/sources/*.yaml` 是内置 catalog 定义，不是运行时 secret 存储。
- 启动时 API 会同步 catalog 到数据库。
- 只有已订阅 source 会被 scheduler 抓取，并默认进入 feed。
- item 入库不等待 AI 摘要完成。
- 抓取、全文和摘要失败都应可观察，但不应阻断历史内容浏览。

## 后端边界

- API 负责 HTTP 接口、settings、source 管理、feed 查询、health 查询和手动触发任务。
- Worker 负责慢任务，不把长耗时工作塞进请求响应路径。
- Scheduler 只负责投递到期任务，真正执行仍交给 worker。
- SQLite 是默认持久层，启用 WAL 和基本 schema migration；Postgres 目前是可选增强路径。

## 前端页面

- `/`：统一 Feed。
- `/sources`：Source Catalog，浏览、筛选、订阅、预览和抓取 source。
- `/sources/new`：新增 source。
- `/health`：运行状态、source health、job 状态和 AI provider 状态。
- `/settings`：运行设置和可选 AI provider 配置。

## AI 摘要

AI provider 是可选增强。未配置 provider 时，系统仍可抓取、阅读、搜索和管理 source。

当前支持两类摘要 provider：

- `openai_compatible`
- `codex_cli`

摘要结果保存 provider、model、usage、状态和错误信息，供 health 与用量页面展示。
