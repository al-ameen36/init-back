# Agents — init-back

Guidance for AI coding agents working in the **Init** backend (FastAPI / Python
3.12).

## Commands

```bash
uv sync                        # install deps
just dev                       # uv run fastapi dev  (http://localhost:8000)
just run                       # uv run fastapi run --host 0.0.0.0 --port 8000
just format                    # ty check && ruff format
```

Run `just format` (or at least `ty check`) after edits.

## Structure

- `main.py` — app, CORS, request-logging middleware, router includes, lifespan
  (inits Supabase).
- `app/auth.py` — `get_current_user` verifies Supabase JWTs via JWKS.
- `supabase_client.py` — async Supabase client (`get_supabase()`).
- `routes/` — one router per domain: `developer` (SSE profile analysis),
  `gh_issues` (`/issues/{repo}`), `repo` (`/repo/{owner}/{name}`),
  `repo_analysis` (`POST /analyze/`), `auth` (`/auth/me`), `github_stats`.
- `features/` — business logic: `developer`, `events` (per-job `asyncio.Queue`),
  `gh_issues`, `github`, `technologies`, `llm`, `search`.
- `models/` — Pydantic request/response models.

## SSE contracts

- Developer analysis: `POST /developer/analyze` → `{job_id}`; the frontend opens
  `GET /developer/events/{job_id}` as an `EventSource`. Each frame is
  `{ "step": ..., "status": ..., "data": {...} }`; the frontend reads the
  nested `data` field. Steps: `profile`, `repositories`, `languages`,
  `technologies`, `pull_requests`, `completed`, plus `error`.
- Issue analysis: `POST /analyze/` streams `text/event-stream` frames
  `{ "type": "status"|"result"|"error", ... }`. A `result` carries
  `analysis: AnalyzeResponse`; an `error` may carry a `number` (per-issue) or
  not (batch-level). The frontend parses these manually in
  `init-front/src/lib/api.ts` (`analyzeIssuesStream`).

## Caching & persistence

- Finished issue analyses are upserted into the Supabase `analysis` table,
  unique on `(repo, issue_number, profile_key)`. Cached results are emitted
  first on the next request (set `force: true` to bypass).
- `profiles` and `repositories` tables back the frontend contexts.

## Conventions

- Keep response models in `models/`. Don't return bare dicts from routers
  without a reason.
- `graph-sitter` `Codebase.from_repo` is slow — build it once per batch and
  reuse (see `routes/repo_analysis.py`).
- Long work must stream; don't block a request waiting for the full batch.

## Gotchas

- `.env` is **not** auto-loaded by `requests`/`pygithub`; the token must be in
  the real environment. `GITHUB_TOKEN` is required to avoid 403s.
- `OPENAI_BASE_URL` / `OPENAI_MODEL` in `.env.example` are placeholders.
- CORS allows only `localhost`/`127.0.0.1` on ports `3000` and `5173`.
- Only `/auth/me` enforces auth today; other routes are public by design.
