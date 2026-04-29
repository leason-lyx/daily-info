# 配置与部署

## Docker Compose 快速启动

```bash
cp .env.example .env
docker compose up -d
```

启动后访问：

- Web：`http://localhost:3000`
- API docs：`http://localhost:8000/docs`

默认 Compose 栈使用 SQLite，数据库文件位于后端容器内的 `/data/daily-info.db`，并通过 `daily_info_data` Docker volume 持久化。

## 常用环境变量

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 数据库连接串。Docker 默认是 `sqlite:////data/daily-info.db`。 |
| `PUBLIC_APP_URL` | 应用公开访问地址。 |
| `API_BASE_URL` | 后端内部 API 地址。 |
| `NEXT_PUBLIC_API_BASE_URL` | 浏览器访问 API 的地址，默认 `http://localhost:8000`。 |
| `RSSHUB_PUBLIC_INSTANCES` | 逗号分隔的公共 RSSHub 实例列表。 |
| `RSSHUB_SELF_HOSTED_BASE_URL` | 可选自托管 RSSHub 地址。 |
| `LLM_PROVIDER_TYPE` | `none`、`openai_compatible` 或 `codex_cli`。 |
| `LLM_BASE_URL` | OpenAI-compatible API base URL。 |
| `LLM_API_KEY` | 可选摘要 API key。 |
| `LLM_MODEL_NAME` | OpenAI-compatible provider 模型名。 |
| `CODEX_CLI_PATH` | Codex CLI 路径。 |
| `CODEX_CLI_MODEL` | 可选 Codex CLI 模型覆盖。 |

## 本地开发环境

宿主机开发请使用 `.env.local.example`，因为 Docker 模板中的 SQLite 路径 `/data` 通常只在容器内存在。

```bash
cp .env.local.example .env.local
set -a
source .env.local
set +a
```

## RSSHub

RSSHub 是可选增强。系统会按配置的实例顺序尝试 RSSHub route，并记录实际成功的实例。

可用策略：

- 使用默认公共 RSSHub 实例。
- 设置 `RSSHUB_SELF_HOSTED_BASE_URL` 指向自托管实例。
- 使用 Compose profile 启动仓库内的 `rsshub` 服务。

## AI Provider

默认 `LLM_PROVIDER_TYPE=none`，不会调用外部 AI 服务。

启用 OpenAI-compatible provider 时，可以先通过环境变量提供一组初始配置：

- `LLM_PROVIDER_TYPE=openai_compatible`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL_NAME`

启动后也可以在 `/settings` 中维护多个自定义 API 配置档。每个配置档包含名称、Base URL、模型、API key、temperature、timeout、启用状态和顺序。摘要任务会按启用配置档的顺序调用；当前一个配置失败时，会在同一次摘要任务内立即尝试下一个启用配置。环境变量和旧版 UI settings 会在没有配置档时作为兼容来源生成默认配置档。

启用 Codex CLI provider 时，需要确保运行环境可以访问 Codex CLI，并配置：

- `LLM_PROVIDER_TYPE=codex_cli`
- `CODEX_CLI_PATH`
- `CODEX_CLI_MODEL`

## 安全注意事项

- 不要提交 `.env`、`.env.local`、数据库文件、日志、私钥或导出 artifacts。
- Source catalog 文件会进入仓库，应视为公开配置。
- API key、cookie、token 等 secret 只能放在运行时配置里。
- 如果部署到 localhost 之外，请重新检查 CORS、网络暴露、代理和数据库凭据。
- Compose 中的可选 Postgres profile 默认值只适合本地开发，不应直接用于公网生产环境。
