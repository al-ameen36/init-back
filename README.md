# Init API

> The backend powering **Init** — an AI onboarding engineer that helps developers understand unfamiliar codebases and contribute to open source faster.

This service orchestrates GitHub, Graph Sitter, LLMs, and Supabase to deliver analyses to the frontend in real time.

---

## Tech Stack

Python 3.12, FastAPI, Graph Sitter, PyGithub, vLLM-served LLMs,
`@specfy/stack-analyser`, Supabase, SSE, `uv`, `just`.

---

## Getting Started

### Install dependencies

```bash
uv sync
```

### Configure environment

```bash
cp .env.example .env
```

```dotenv
GITHUB_TOKEN=ghp_xxx
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=http://localhost:30000/v1
OPENAI_MODEL=google/gemma-4-31B-it
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SECRET_KEY=<service-role-key>
SUPABASE_JWKS_URL=https://<project>.supabase.co/auth/v1/keys
```

The GitHub token is required to avoid API rate limits.

### Database setup (Supabase)

Run the SQL files in `sql/` against your Supabase project:

```bash
supabase db execute --file sql/profiles.sql
supabase db execute --file sql/repositories.sql
supabase db execute --file sql/analyses.sql
```

> RLS is enabled on every table. `profiles`/`repositories` are user-scoped.
> `analysis` is written by the backend with the service-role key (bypasses RLS).

---

## Running

```bash
just dev      # Development server (http://localhost:8000)
just run      # Production server
just format   # ty check && ruff format
```

Interactive docs at `http://localhost:8000/docs`.

---

## Docker

`docker-compose.yml` defines two services: `backend` (FastAPI) and `memgraph`
(code-graph store). The backend image bundles Node.js + `@specfy/stack-analyser`.

```bash
docker compose up --build        # foreground
docker compose up -d --build     # detached
docker compose down -v           # stop + remove memgraph volume
```

Set `GITHUB_TOKEN`, `SUPABASE_*`, and `OPENAI_*` in `.env` before building.
The API is available at `http://localhost:8000`.

---

## Authentication

Supabase GitHub OAuth. Every route except `/health` requires a valid JWT:

```
Authorization: Bearer <access_token>
```

Verified against `SUPABASE_JWKS_URL`. The `get_current_user` dependency
provides `id`, `email`, and `github_username`.

---

## API Overview

| Method | Endpoint                     | Description                    |
| ------ | ---------------------------- | ------------------------------ |
| POST   | `/developer/analyze`         | Start developer profile analysis |
| GET    | `/developer/events/{job_id}` | SSE stream for developer analysis |
| GET    | `/repo/{owner}/{name}`       | Repository information         |
| GET    | `/issues/{repo:path}`        | List repository issues         |
| POST   | `/analyze/`                  | Analyze issues (SSE stream)    |
| POST   | `/pr-pattern/analyze`        | PR pattern analysis (SSE)      |
| POST   | `/pr-pattern/pr`             | Single PR analysis             |
| GET    | `/github/stats`              | GitHub contribution stats      |
| GET    | `/auth/me`                   | Authenticated user             |
| GET    | `/health`                    | Health check                   |

---

## Analysis Pipeline

```text
GitHub Issue → Search Queries → Graph Sitter Search → Rank Files
  → LLM Investigation Guide → Stream to Frontend → Cache in Supabase
```

Each result includes: match score, relevant files, investigation guide,
implementation path, and supporting context.

---

## Project Structure

```text
main.py
├── features/          # business logic
├── routes/            # one router per domain
├── pr_pattern_analyzer/  # PR pattern analysis
├── graph_store/       # Memgraph persistence layer
├── models/            # shared Pydantic models
├── sql/               # Supabase schemas
└── tools/             # helper scripts
```

---

## Related Projects

- **[init-front](https://github.com/al-ameen36/init-front)** — React/TanStack Start frontend.
