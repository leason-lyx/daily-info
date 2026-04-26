# Daily Info

Daily Info is a self-hosted research reading desk for papers, blogs, and RSSHub-backed posts. The current implementation follows `PRD.md` and `docs/design.md`.

## Run with Docker Compose

```bash
cp .env.example .env
docker compose up -d
```

Open:

- Web: http://localhost:3000
- API docs: http://localhost:8000/docs

The default stack uses SQLite at `/data/daily-info.db` inside the API/worker/scheduler containers, backed by the `daily_info_data` Docker volume. It does not require AI keys, Postgres, RSSHub, or cloud services.

Default sources are loaded from `config/source-packs/default.yaml` at startup and then synchronized into the database. The Docker API image copies `config/source-packs`, so Docker Compose uses the same default pack as local development.

Secrets belong in `.env` or `.env.local`, not in source packs. The Docker build context excludes `.env.*` files except the checked-in example templates.

## Local development

Use the local env template instead of the Docker template. The Docker template points SQLite at `/data`, which usually does not exist on the host machine.

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

Fetch every registered source once and audit whether each source provides feed
fulltext, feed summaries, title-only entries, detail-page fulltext, paper
abstracts, or fetch failures:

```bash
python -m app.source_audit
```

The audit command backs up the SQLite database first, applies the recommended
default-pack fulltext strategies, fetches disabled sources without changing
their enabled state, and writes a JSON report under `artifacts/`.

Frontend:

```bash
cd web
npm install
npm run dev
```

If `npm run dev` reports `Watchpack Error (watcher): Error: EMFILE: too many open files`, use the polling dev server:

```bash
npm run dev:poll
```

For browser checks that do not need hot reload, the production path is the most stable:

```bash
npm run build
npm run start
```

## MVP surface

- Unified feed with type, source, time, search, summary status, read/star/hidden filters.
- Source Registry with default-pack and custom rows using the same source config shape.
- `/sources/new` supports RSS/Atom, RSSHub route, and HTML fallback preview before save.
- `/health` separates source fetch health, fulltext health, summary state, job state and AI provider status.
- Optional OpenAI-compatible and Codex CLI summary provider boundaries.
