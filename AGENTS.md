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
- `features/` — business logic:
  - `auth.py` — `get_current_user` verifies Supabase JWTs via JWKS.
  - `supabase.py` — async Supabase client (`get_supabase()`).
  - `graph_sitter_patch.py` — side-effect import that makes graph_sitter
    unit-fault-tolerant (recursion limits, body-less symbols, unresolvable
    imports, per-unit dependency failures, top-level `build_graph` safety net).
  - `developer.py` / `developer_models.py` — developer profile analysis + Pydantic models.
  - `events.py` — per-job `asyncio.Queue` for SSE.
  - `code_graph.py` — bridges `graph_sitter.Codebase` and the graph store.
    `ensure_graph(repo)` parses once and persists to Memgraph; `graph_search`
    + `list_file_paths` query the store without a live `Codebase`.
  - `github.py`, `gh_issues.py`, `technologies.py`, `llm.py`, `search.py`.
- `routes/` — one router per domain: `developer`, `gh_issues`, `repo`,
  `repo_analysis`, `auth`, `github_stats`, `pr_pattern`.
- `pr_pattern_analyzer/` — PR pattern analysis: `evidence`, `playbook`, `models`.
- `graph_store/` — code-graph persistence. `GraphStore` interface
  (`graph_store/base.py`); `MemgraphGraphStore` (Bolt) and `InMemoryGraphStore`
  (test double). Get via `graph_store.get_graph_store()` (reads `MEMGRAPH_URI`).
- `models/` — shared Pydantic request/response models.
- `sql/` — Supabase table schemas and RLS policies.
- `tools/` — helper scripts (e.g. `analyse.mjs` for `@specfy/stack-analyser`).

## SSE contracts

- Developer analysis: `POST /developer/analyze` → `{job_id}`; frontend opens
  `GET /developer/events/{job_id}`. Frames: `{ "step", "status", "data" }`.
  Steps: `profile`, `repositories`, `languages`, `technologies`,
  `pull_requests`, `completed`, `error`.
- Issue analysis: `POST /analyze/` streams `{ "type": "status"|"result"|"error" }`.
  A `result` carries `analysis: AnalyzeResponse`.

## Conventions

- Keep response models in `models/`. Don't return bare dicts.
- `Codebase.from_repo` is slow — build once per batch and reuse.
- Long work must stream; don't block a request for the full batch.

## Gotchas

- `.env` is **not** auto-loaded by `requests`/`pygithub`. `GITHUB_TOKEN` is
  required to avoid 403s.
- All API routes require a valid Supabase JWT via `Depends(get_current_user)`.
- Docker Compose runs Memgraph as a separate service (`bolt://memgraph:7687`).
- CORS allows only `localhost`/`127.0.0.1` on ports `3000` and `5173`.
