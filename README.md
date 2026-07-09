# Init API (backend)

FastAPI backend that analyzes a GitHub developer profile and streams the
progress to the frontend over Server-Sent Events (SSE).

## Stack

- Python 3.12 (pinned in `.python-version`)
- FastAPI + `sse-starlette` for streaming
- `pygithub` for GitHub data
- `graph-sitter` for code/technology extraction
- `openai` for any LLM-assisted steps
- `uv` for dependency management

## Setup

```bash
# install dependencies
uv sync

# configure environment
cp .env.example .env   # then fill in at least GITHUB_TOKEN
```

The backend requires a GitHub token to avoid unauthenticated `403 rate limit
exceeded` errors. `requests`/`pygithub` do **not** auto-load `.env`, so the
token must be present in the real environment when the server runs.

```dotenv
# .env
GITHUB_TOKEN=ghp_xxx
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
HF_TOKEN=hf_xxx
```

## Running

```bash
just dev          # uv run fastapi dev  (http://localhost:8000)
```

The server logs every request (`main.py` middleware) plus granular logs in
`routes/`, `features/`, and `github.py` (including `authenticated=True/False`).

## Endpoints

| Method | Path                       | Description                                            |
| ------ | -------------------------- | ------------------------------------------------------ |
| GET    | `/health`                  | Liveness check.                                        |
| POST   | `/developer/analyze`       | Start a profile analysis, returns `{ job_id }`.        |
| GET    | `/developer/events/{job_id}` | SSE stream of analysis events for a job.            |
| POST   | `/analyze`                 | Analyze a single issue (dashboard feature).            |
| GET    | `/issues/{repo}`           | Fetch issues for a repo.                               |
| GET    | `/repo/...`                | Repo analysis endpoints.                               |

CORS is restricted to `http://localhost:3000` and `http://127.0.0.1:3000`.

## Developer analysis flow

`POST /developer/analyze` accepts `{ "username": "<github user>" }` and starts
a background task (`features/developer.py`). It emits SSE events in this order:

1. `profile` – basic user info (`username`, `avatar_url`, `name`, `bio`)
2. `repositories` – repo list (name, language, stars) + count
3. `languages` – primary languages derived from repos
4. `technologies` – extracted packages/tech stack
5. `pull_requests` – PR/contribution stats
6. `completed` – full aggregated profile payload

On failure an `error` event is emitted with `{ "message": "..." }`.

Each SSE message is serialized as:

```json
{ "step": "repositories", "status": "completed", "data": { "...": "..." } }
```

The frontend reads the payload from the nested `data` field.

Events are produced by `features/events.py` (a per-job `asyncio.Queue` keyed by
job id) and consumed by the SSE route in `routes/developer.py`.

> The frontend currently hardcodes the demo user `yyx990803` in
> `useOnboardingAnalysis.ts`; no auth/input is wired up yet.

## Project layout

```
main.py                 FastAPI app, CORS, request logging
routes/                 API routers (developer, issues, repo analysis)
features/               business logic (github, developer, events, technologies, llm, search)
models/                 Pydantic request/response models
```
