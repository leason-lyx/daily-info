# Daily Info

[English](README.md) | [简体中文](README_zh-CN.md)

Daily Info 是一个自托管的信息阅读台，用来聚合论文、工程博客、AI 实验室动态、科技媒体和基于 RSSHub 的信息源。它会把订阅源整理成可搜索的时间线，展示抓取与摘要状态，并可选地通过 OpenAI-compatible 接口或 Codex CLI 生成摘要。

## 功能特性

- 统一 Feed：支持论文、博客、帖子，带搜索、来源筛选、确定性跨来源去重、摘要状态、已读/收藏/隐藏和按来源策略清洗/生成的标签。
- Source Catalog：内置信息源定义来自 `config/sources/*.yaml`，用户显式订阅后才会抓取并进入默认 Feed。
- 信息源预览与创建：支持 RSS/Atom、RSSHub route，以及 HTML 列表页 fallback。
- 后台 worker 和 scheduler：负责抓取、全文提取和可选的自动摘要任务。
- 健康页：展示 source run、全文覆盖、摘要状态、任务状态和 AI provider 状态。
- 可选摘要 provider：支持多个 OpenAI-compatible 自定义 API 按顺序降级，以及 Codex CLI。
- 默认 Docker Compose + SQLite 部署；不需要云服务，也不需要 AI key 就能启动。

## 常用入口

- Web 应用：`http://localhost:3000`
- API 文档：`http://localhost:8000/docs`
- 健康页：`http://localhost:3000/health`
- Source Catalog：`http://localhost:3000/sources`

## 快速开始

推荐使用 Docker Compose 启动本地部署：

```bash
cp .env.example .env
docker compose up -d
```

然后打开 `http://localhost:3000`。

默认 Compose 栈包含：

- `api`：FastAPI 后端，监听 `127.0.0.1:8000`
- `web`：Next.js 前端，监听 `127.0.0.1:3000`
- `worker`：后台任务执行器
- `scheduler`：周期性抓取与摘要调度器

默认 SQLite 数据库位于后端容器内的 `/data/daily-info.db`，并通过 `daily_info_data` Docker volume 持久化。

如果要通过 Tailscale 在笔记本访问，保持容器只监听 localhost，让 Tailscale Serve 占用 tailnet 对外端口：

```bash
sudo tailscale serve --https=443 http://127.0.0.1:3000
sudo tailscale serve --https=8000 http://127.0.0.1:8000
```

## 配置

Daily Info 从环境变量读取运行时配置，部分可在 UI 中修改的配置会保存到数据库。

常见配置：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 数据库连接串。Docker 默认是 `sqlite:////data/daily-info.db`。 |
| `NEXT_PUBLIC_API_BASE_URL` | 浏览器访问后端 API 的地址，默认 `http://localhost:8000`；如果页面不是从 localhost 打开，前端会把 localhost API 地址自动改成当前 hostname 的 `:8000`，适配 Tailscale Serve。 |
| `RSSHUB_PUBLIC_INSTANCES` | 逗号分隔的公共 RSSHub 实例列表。 |
| `RSSHUB_SELF_HOSTED_BASE_URL` | 可选的自托管 RSSHub 实例。 |
| `LLM_PROVIDER_TYPE` | `none`、`openai_compatible` 或 `codex_cli`。 |
| `LLM_BASE_URL` | 初始 OpenAI-compatible API base URL；更多自定义 API 配置档可在 Settings 中管理。 |
| `LLM_API_KEY` | 可选的初始摘要 API key。 |
| `LLM_MODEL_NAME` | 初始 OpenAI-compatible provider 模型名。 |
| `CODEX_CLI_PATH` | 使用 Codex 摘要 provider 时的 Codex CLI 路径。 |
| `CODEX_CLI_MODEL` | 可选的 Codex CLI 模型覆盖。 |

敏感信息应该放在 `.env` 或 `.env.local`，不要写进 source catalog。Docker build context 会排除 `.env.*`，只保留仓库中的示例模板。

## Source Catalog

内置信息源定义位于 `config/sources/*.yaml`。应用启动时会把它们同步进数据库，作为可浏览的 catalog。Source Catalog 页面可以编辑自动摘要策略、抓取周期、全文策略、标签策略、默认标签、过滤词、分组、优先级和语言等低风险配置；保存后会写回 YAML，再同步数据库。

Catalog 是显式订阅模式：

- 只有已订阅 source 会被 scheduler 抓取。
- 只有已订阅 source 会进入默认 Feed。
- 未订阅 source 仍会显示在 Source Catalog 中，便于发现和开启。

当同一内容出现在多个 source 中时，Daily Info 只保存一条由 `dedupe_key` 标识的 item，并通过 `item_sources` 记录全部来源。Feed 和 API 会通过 `sources[]` 返回这些来源；单个 `source_id/source_name` 表示主展示来源。

Source definition 可以包含抓取方式、全文策略、摘要策略、过滤规则、标签、分组和元数据。它们应该被视为公开配置，不应包含 API key、cookie、token 或其他 secret；通过网页编辑时也必须遵守这一点。未来如果某个 source 需要认证，catalog 中只保存 secret 引用名，真实 secret 放在运行时配置里。

X/Twitter 社交媒体 source 默认使用 RSSHub route，例如 [`/twitter/user/:id/:routeParams?`](https://docs.rsshub.app/routes/popular)。公共 RSSHub 实例可以免费尝试，但 X route 稳定性不保证；如果以后需要更稳定的 X 抓取，可以自建 RSSHub，并把官方推荐的 `TWITTER_AUTH_TOKEN` 配在 source catalog 之外。

## 本地开发

宿主机本地开发请使用 `.env.local.example`。Docker 用的 `.env.example` 会把 SQLite 指向 `/data`，这个路径通常只在容器内存在。

```bash
cp .env.local.example .env.local
set -a
source .env.local
set +a
```

后端：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
uvicorn app.api:app --reload
```

Worker 和 scheduler：

```bash
python -m app.worker
python -m app.scheduler
```

前端：

```bash
cd web
npm install
npm run dev
```

如果开发服务器遇到文件监听数量限制，可以使用 polling：

```bash
npm run dev:poll
```

如果要走更接近生产的前端路径：

```bash
npm run build
npm run start
```

## Source Audit

可以运行 source audit，一次性抓取 source 并检查内容质量：

```bash
python -m app.source_audit
```

该命令会先备份 SQLite 数据库，在不改变订阅状态的情况下抓取 source，并在 `artifacts/` 下写出 JSON 报告。

## 测试

后端测试：

```bash
uv run pytest
```

前端检查：

```bash
cd web
npm run lint
npm run build
```

验收测试建议使用 Docker Compose，这样验证路径和本地部署一致：

```bash
docker compose up --build -d --force-recreate api worker scheduler web
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/source-definitions
```

如果改动涉及 UI 或 Feed 行为，还需要打开 Docker Compose 启动的前端页面，在浏览器里验证受影响流程。

## 安全说明

- 不要提交 `.env`、`.env.local`、数据库文件、日志、私钥或导出的 artifacts。
- `.gitignore` 和 `.dockerignore` 已排除本地 secret、缓存、数据库和构建产物。
- Source catalog 文件是公开配置，不应包含 secret 值。
- 默认 AI provider 关闭；只有在配置 provider 后才会调用外部摘要服务。Settings 可保存多个 OpenAI-compatible 自定义 API 配置档，启用的配置会按顺序调用，并在同一次摘要任务内自动降级到备用配置。
- 如果部署到 localhost 以外的环境，请重新检查 CORS、网络暴露、代理配置和数据库凭据。

## 项目状态

项目仍处于早期阶段。更详细的维护文档位于 [`docs/README.md`](docs/README.md)。

当前还没有选择开源许可证。在添加 LICENSE 之前，复用和再分发权利并未被明确授予。

## 贡献

仓库公开后欢迎 issue 和 pull request。较大的改动建议先开 issue 讨论设计。

推荐 PR 检查清单：

- Source catalog 不包含 secret。
- 跑后端和前端检查。
- 涉及功能行为或 UI 的改动，通过 Docker Compose 启动服务并在浏览器中验收。
- 如果 setup、配置或用户可见行为发生变化，同步更新文档。
