# Init API

> The backend powering **Init** — an AI onboarding engineer that helps developers understand unfamiliar codebases and contribute to open source faster.

Instead of spending hours navigating an unfamiliar repository, Init analyzes a GitHub issue, understands the codebase, identifies the most relevant parts of the project, and generates a step-by-step investigation plan tailored to the developer.

This service orchestrates GitHub, Graph Sitter, LLMs, and Supabase to deliver analyses to the frontend in real time.

---

## What it does

- 🧑‍💻 Analyzes GitHub developer profiles
- 📦 Understands repository architecture using Graph Sitter
- 🎯 Matches developers with issues suited to their experience
- 🔍 Identifies the files and execution paths relevant to an issue
- 📝 Generates investigation guides for solving issues
- ⚡ Streams long-running analyses with Server-Sent Events (SSE)
- 💾 Caches completed analyses in Supabase for fast subsequent requests

---

## Architecture

```text
                 GitHub
                    │
     ┌──────────────┴──────────────┐
     │                             │
Developer Profile          Repository + Issues
     │                             │
     └──────────────┬──────────────┘
                    │
                    ▼
             Graph Sitter Analysis
                    │
                    ▼
              LLM Investigation
                    │
                    ▼
         Investigation Guide + Match Score
                    │
                    ▼
          Streamed to Frontend (SSE)
                    │
                    ▼
             Cached in Supabase
```

---

## Tech Stack

- Python 3.12
- FastAPI
- Graph Sitter
- vLLM-served LLMs (e.g. `google/gemma-4-31B-it`)
- `@specfy/stack-analyser` (tech detection for the skills dashboard)
- PyGithub
- Supabase
- SSE (Server-Sent Events)
- `uv`
- `just`

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

Required environment variables:

```dotenv
GITHUB_TOKEN=ghp_xxx

# Any non-empty value works when pointing at a local vLLM server.
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=http://localhost:30000/v1
OPENAI_MODEL=google/gemma-4-31B-it

SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SECRET_KEY=<service-role-key>
SUPABASE_JWKS_URL=https://<project>.supabase.co/auth/v1/keys
```

The GitHub token is required to avoid API rate limits when fetching repositories and issues.

### Database setup (Supabase)

Init persists data in three Supabase tables. Their schemas, indexes, and Row
Level Security policies live in `sql/`:

| File                   | Table         | Written by | Purpose                                  |
| ---------------------- | ------------- | ---------- | ---------------------------------------- |
| `sql/profiles.sql`     | `profiles`    | frontend   | developer profile, languages, tech stack |
| `sql/repositories.sql` | `repositories`| frontend   | user-managed GitHub repositories         |
| `sql/analyses.sql`     | `analysis`    | backend    | cached issue investigations              |

Run all three in your Supabase database. They are idempotent
(`create table if not exists` + policy drops), so re-running is safe.

- **Supabase SQL Editor:** paste each file's contents and run it.
- **Supabase CLI:**
  ```bash
  supabase db execute --file sql/profiles.sql
  supabase db execute --file sql/repositories.sql
  supabase db execute --file sql/analyses.sql
  ```
- **psql** against your project's connection string:
  ```bash
  psql "$SUPABASE_DB_URL" -f sql/profiles.sql
  psql "$SUPABASE_DB_URL" -f sql/repositories.sql
  psql "$SUPABASE_DB_URL" -f sql/analyses.sql
  ```

> RLS is enabled on every table. `profiles`/`repositories` are scoped so a user
> can only read/write their own rows (the frontend authenticates with the
> publishable key, so `auth.uid()` is enforced). `analysis` is written by the
> backend using the **service-role** key, which bypasses RLS.

### Model (vLLM on AMD Developer Cloud)

Init talks to an OpenAI-compatible endpoint. The model is served with vLLM on the
**AMD Developer Cloud** (using AMD GPUs, hence `HIP_VISIBLE_DEVICES`):

```bash
HIP_VISIBLE_DEVICES=0 vllm serve google/gemma-4-31B-it \
    --gpu-memory-utilization 0.8 \
    --dtype bfloat16 \
    --tensor-parallel-size 1 \
    --host 0.0.0.0 \
    --port 30000 \
    --max-num-seqs 128 \
    --max-num-batched-tokens 8192 \
    --max-model-len 8192 \
    --distributed-executor-backend mp
```

Point `OPENAI_BASE_URL` at the AMD Developer Cloud endpoint that exposes vLLM on
port `30000` (e.g. `http://<amd-cloud-host>:30000/v1`) and set `OPENAI_MODEL` to the
served model name (`google/gemma-4-31B-it`). Any OpenAI-compatible endpoint works by
changing those two variables.

---

## Running

```bash
just dev      # Development server
just run      # Production server
just format   # Format & lint
```

The API is available at:

```
http://localhost:8000
```

Interactive documentation:

```
http://localhost:8000/docs
```

---

## Running with Docker

`docker-compose.yml` defines a single `backend` service. The image is built
multi-stage: it copies the Node.js runtime from `node:20-bookworm-slim` onto the
`uv` Python base and pre-installs `@specfy/stack-analyser` at `/opt/analyser`, so
tech detection works with no extra setup or sidecar.

### Build & run

```bash
# from the repo root
docker compose up --build        # foreground logs
docker compose up -d --build     # detached
```

The API is then available at `http://localhost:8000` (the service publishes port
`8000`).

### Walkthrough

1. **Configure env.** Copy and fill in the environment first — the container
   reads it via `env_file:`:
   ```bash
   cp .env.example .env
   ```
   Set `GITHUB_TOKEN`, the `SUPABASE_*` vars (pointing at your Supabase project,
   whose tables you created in *Database setup*), and the `OPENAI_*` vars pointing
   at your AMD Developer Cloud vLLM endpoint (`OPENAI_BASE_URL=http://<host>:30000/v1`,
   `OPENAI_MODEL=google/gemma-4-31B-it`).
2. **Build the image.** `docker compose up --build` compiles the Python deps with
   `uv sync` and installs the Node analyser once. Subsequent builds are cached.
3. **Verify.** Open `http://localhost:8000/docs` or hit `http://localhost:8000/health`.

### Notes

- No `STACK_ANALYSER_URL` is needed — the analyser is bundled in the image
  (`tools/analyse.mjs` + `/opt/analyser`).
- There is **no database container** in this compose file. `SUPABASE_URL` points
  at your external Supabase project; run the `sql/*.sql` files there.
- `docker compose down` stops the service. This file defines no named volumes,
  so there is nothing extra to clean up.

---

## Authentication

Authentication is handled by **Supabase GitHub OAuth**.

The frontend authenticates users, while the backend verifies the JWT using Supabase's JWKS endpoint before exposing authenticated resources.

Currently, only:

```
GET /auth/me
```

requires authentication.

---

## API Overview

### Developer

| Method | Endpoint                     |
| ------ | ---------------------------- |
| POST   | `/developer/analyze`         |
| GET    | `/developer/events/{job_id}` |

Analyzes a GitHub profile and streams the resulting developer profile over SSE.

---

### Repository

| Method | Endpoint               |
| ------ | ---------------------- |
| GET    | `/repo/{owner}/{name}` |
| GET    | `/issues/{repo:path}`  |
| POST   | `/analyze/`            |

Fetch repository information, list issues, and analyze issues against a developer profile.

---

### Authentication

| Method | Endpoint   |
| ------ | ---------- |
| GET    | `/auth/me` |

Returns the authenticated user.

---

### Utility

| Method | Endpoint        |
| ------ | --------------- |
| GET    | `/health`       |
| GET    | `/github/stats` |

---

## Analysis Pipeline

When analyzing a repository issue, Init performs the following steps:

```text
GitHub Issue
      │
      ▼
Generate Search Queries
      │
      ▼
Search Repository with Graph Sitter
      │
      ▼
Rank Relevant Files
      │
      ▼
Generate Investigation Guide
      │
      ▼
Stream Results to Frontend
      │
      ▼
Cache Analysis in Supabase
```

Each completed issue analysis contains:

- Match score
- Relevant files
- Investigation guide
- Suggested implementation path
- Supporting context

---

## Developer Analysis

Developer analysis builds a profile from public GitHub activity by collecting:

- Basic profile information
- Repository history
- Languages
- Technologies
- Contribution history
- Pull request statistics

Results are streamed progressively over SSE so the frontend can update in real time.

---

## Caching

Completed issue analyses are cached in Supabase using:

```
(repo, issue_number, profile_key)
```

Subsequent requests for the same developer and issue return instantly without repeating expensive analysis.

---

## Project Structure

```text
main.py
├── app/
│   └── auth.py
├── routes/
├── features/
├── models/
├── supabase_client.py
└── ...
```

---

## Roadmap

- ✅ GitHub profile analysis
- ✅ Repository issue analysis
- ✅ Investigation guides
- ✅ Live progress streaming
- ✅ Cached analyses
- ⏳ Pull request generation
- ⏳ Repository onboarding
- ⏳ Interactive code walkthroughs
- ⏳ Team knowledge sharing

---

## Why Init?

Understanding an unfamiliar codebase is often the hardest part of contributing to open source.

Init reduces the time spent figuring out **where to start** by generating an investigation plan before a developer writes a single line of code.

Instead of searching through dozens of files, developers can focus on implementing the solution.

---

## Related Projects

- **[init-front](https://github.com/al-ameen36/init-front)** — the React/TanStack Start web application that powers the Init experience (onboarding, issue matching, skills dashboard, and investigation guides). This backend serves its API.
