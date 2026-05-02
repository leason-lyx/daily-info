# Daily Info

[English](README.md) | [简体中文](README_zh-CN.md)

Daily Info is a self-hosted reading desk for research papers, engineering blogs, AI lab updates, tech media, and RSSHub-backed feeds. It collects sources into a searchable timeline, keeps source health visible, and can optionally generate summaries with an OpenAI-compatible provider or Codex CLI.

## Features

- Unified feed for papers, blogs, and posts, with search, source filters, deterministic cross-source deduplication, summary status, read/star states, and source-aware cleaned/generated tags.
- Source Catalog backed by `config/sources/*.yaml`, with explicit subscriptions so only chosen sources are fetched and shown in the default feed.
- Source preview and creation flow for RSS/Atom feeds, RSSHub routes, and HTML index fallback.
- Background worker and scheduler for source fetching, fulltext extraction, and optional auto-summary jobs.
- Health dashboard for source runs, fulltext coverage, summary state, job state, and AI provider status.
- Optional OpenAI-compatible summary providers with ordered fallback, plus Codex CLI.
- Docker Compose deployment with SQLite by default; no cloud services or AI keys are required to run the app.

## Screens

- Web app: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Health page: `http://localhost:3000/health`
- Source Catalog: `http://localhost:3000/sources`

## Quick Start

The recommended local deployment path is Docker Compose.

```bash
cp .env.example .env
docker compose up -d
```

Open `http://localhost:3000`.

The default Compose stack starts:

- `api`: FastAPI backend on `127.0.0.1:8000`
- `web`: Next.js frontend on `127.0.0.1:3000`
- `worker`: background job runner
- `scheduler`: periodic fetch and summary scheduler

SQLite data is stored at `/data/daily-info.db` inside the backend containers and persisted in the `daily_info_data` Docker volume.

For tailnet access, keep the containers bound to localhost and let Tailscale Serve own the tailnet-facing ports:

```bash
sudo tailscale serve --https=443 http://127.0.0.1:3000
sudo tailscale serve --https=8000 http://127.0.0.1:8000
```

## Configuration

Daily Info reads runtime settings from environment variables and, for some UI-managed settings, from the database.

Common settings:

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | Database connection string. Docker defaults to `sqlite:////data/daily-info.db`. |
| `NEXT_PUBLIC_API_BASE_URL` | API URL used by the browser. Defaults to `http://localhost:8000`; when a localhost value is used from a non-localhost page, the frontend rewrites it to the current hostname on port `8000` for Tailscale Serve access. |
| `RSSHUB_PUBLIC_INSTANCES` | Comma-separated public RSSHub instances used for RSSHub routes. |
| `RSSHUB_SELF_HOSTED_BASE_URL` | Optional private RSSHub instance. |
| `LLM_PROVIDER_TYPE` | `none`, `openai_compatible`, or `codex_cli`. |
| `LLM_BASE_URL` | Initial OpenAI-compatible API base URL. Additional custom API profiles can be managed in Settings. |
| `LLM_API_KEY` | Optional initial API key for summary generation. |
| `LLM_MODEL_NAME` | Initial model name for the OpenAI-compatible provider. |
| `CODEX_CLI_PATH` | Path to the Codex CLI when using the Codex summary provider. |
| `CODEX_CLI_MODEL` | Optional Codex CLI model override. |

Secrets belong in `.env` or `.env.local`, never in source catalog files. The Docker build context excludes `.env.*` files except the checked-in example templates.

## Source Catalog

Built-in source definitions live in `config/sources/*.yaml`. They are synchronized into the database at startup as catalog entries. The Source Catalog page can edit safe per-source options such as auto-summary policy, fetch interval, fulltext policy, tagging policy, default tags, filters, group, priority, and language; those edits are written back to the YAML file and then synchronized into the database.

Catalog entries are opt-in:

- Subscribed sources are scheduled for fetch.
- Subscribed sources appear in the default feed.
- Available but unsubscribed sources stay visible in the Source Catalog for discovery.

When the same content appears in multiple sources, Daily Info stores one item keyed by `dedupe_key` and records every source in `item_sources`. Feed and API responses expose those origins through `sources[]`; the single `source_id/source_name` pair is the primary source for display.

Source definition files may include fetch attempts, fulltext policy, summary policy, filters, tags, grouping, and metadata. They should not contain API keys, cookies, tokens, or other secrets, including when edited from the web UI. If a source eventually needs credentials, store only a secret reference in catalog metadata and keep the secret value in runtime configuration.

## Local Development

Use `.env.local.example` for host-machine development. The Docker `.env.example` points SQLite at `/data`, which is usually only valid inside containers.

```bash
cp .env.local.example .env.local
set -a
source .env.local
set +a
```

Backend:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
uvicorn app.api:app --reload
```

Worker and scheduler:

```bash
python -m app.worker
python -m app.scheduler
```

Frontend:

```bash
cd web
npm install
npm run dev
```

If the dev server hits file watcher limits, use polling:

```bash
npm run dev:poll
```

For a production-like frontend check:

```bash
npm run build
npm run start
```

## Source Audit

Run the source audit command to fetch sources once and inspect content quality:

```bash
python -m app.source_audit
```

The audit backs up the SQLite database first, fetches sources without changing their subscription state, and writes a JSON report under `artifacts/`.

## Testing

Backend tests:

```bash
uv run pytest
```

Frontend checks:

```bash
cd web
npm run lint
npm run build
```

For acceptance testing, use Docker Compose so validation follows the same runtime path as local deployment:

```bash
docker compose up --build -d --force-recreate api worker scheduler web
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/source-definitions
```

For UI or feed behavior changes, also open the Compose-served web app in a browser and verify the affected workflow.

## Security Notes

- Do not commit `.env`, `.env.local`, database files, logs, private keys, or exported artifacts.
- `.gitignore` and `.dockerignore` exclude local secrets, caches, databases, and build outputs.
- Source catalog files are intended to be public configuration and must not contain secret values.
- The default AI provider is disabled. Summary generation only uses an external provider after you configure it. Settings can store multiple OpenAI-compatible custom API profiles; enabled profiles are tried in order, with automatic fallback inside the same summary task.
- If deploying beyond localhost, review CORS, network exposure, proxy settings, and database credentials.

## Repository Status

This project is early-stage. Detailed maintenance docs live in [`docs/README.md`](docs/README.md).

No open-source license has been selected yet. Until a license is added, reuse and redistribution rights are not explicitly granted.

## Contributing

Issues and pull requests are welcome once the repository is made public. For larger changes, please open an issue first so the design can be discussed.

Recommended PR checklist:

- Keep source catalog changes free of secrets.
- Run backend and frontend checks.
- Validate functional UI changes in a browser through Docker Compose.
- Update documentation when setup, configuration, or user-visible behavior changes.
