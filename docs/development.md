# 开发与测试

## 后端开发

```bash
cp .env.local.example .env.local
set -a
source .env.local
set +a

python3 -m venv .venv
. .venv/bin/activate
pip install -e .
uvicorn app.api:app --reload
```

后台进程：

```bash
python -m app.worker
python -m app.scheduler
```

## 前端开发

```bash
cd web
npm install
npm run dev
```

如果遇到文件监听数量限制：

```bash
npm run dev:poll
```

生产路径检查：

```bash
npm run build
npm run start
```

## 测试命令

后端：

```bash
uv run pytest
```

前端：

```bash
cd web
npm run lint
npm run build
```

验收测试必须通过 Docker Compose 覆盖本地部署路径：

```bash
docker compose -f docker-compose.yml -f docker-compose.localhost.yml up --build -d --force-recreate api worker scheduler web
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/source-definitions
```

`docker-compose.yml` 是普通 bridge network 的 base 配置，不直接发布端口；`docker-compose.localhost.yml` 仍使用 bridge network，但把 Web 和 API 端口发布到宿主机回环地址：`127.0.0.1:3000` 和 `127.0.0.1:8000`。如果需要远程访问，推荐继续用 Tailscale Serve 转发这些 localhost 端口，而不是让容器直接监听所有网卡。

localhost override 使用 bridge network 运行本机部署，因此容器内的 `127.0.0.1` 指向容器自身。需要容器走宿主机代理时，在 `.env` 中设置 `HTTP_PROXY`、`HTTPS_PROXY` 和 `ALL_PROXY` 为 Docker Desktop 可访问的宿主机地址，例如 `http://host.docker.internal:7890`。

本轮 schema 重构允许重建本地 SQLite 数据。需要重置 Docker volume 时：

```bash
docker compose -f docker-compose.yml -f docker-compose.localhost.yml down -v
docker compose -f docker-compose.yml -f docker-compose.localhost.yml up --build -d api worker scheduler web
```

涉及 UI 或功能行为的改动，还需要在浏览器中验证受影响页面。

## Source Audit

```bash
python -m app.source_audit
```

该命令会备份 SQLite 数据库，抓取 source 并输出内容质量报告到 `artifacts/`。报告可用于判断 feed 是全文、摘要、标题列表还是需要详情页抽取。

## 贡献检查清单

提交前确认：

- 没有提交 `.env`、数据库、日志、私钥、node_modules 或构建产物。
- Source catalog 不包含真实 secret。
- 后端和前端检查已按改动风险运行。
- 功能性改动已通过 Docker Compose 和浏览器验收。
- README 或 docs 已随 setup、配置、API 或用户可见行为变化同步更新。

## 发布建议

默认通过分支和 pull request 提交，不直接推送到默认分支。较大的功能改动建议在 PR 描述中写清：

- 改了什么。
- 为什么改。
- 对用户或部署的影响。
- 验证命令和浏览器验收结果。
