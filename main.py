import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager

import features.graph_sitter_patch  # noqa: F401  side-effect: make graph_sitter tolerate unparseable files

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from features.supabase import init_supabase
from routes import (
    developer_router,
    issues_router,
    repo_router,
    repo_meta_router,
    auth_router,
    github_router,
    pr_pattern_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("init")

# Origins allowed to call the API. In production set ALLOWED_ORIGINS to a
# comma-separated list including your Vercel URL, e.g.
# "https://init.vercel.app,http://localhost:3000".
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_supabase()
    logger.info("Supabase client initialized")
    yield
    logger.info("Shutting down")


app = FastAPI(title="Init API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next: Callable) -> object:
    logger.info("%s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error in %s %s", request.method, request.url.path)
        raise
    logger.info(
        "%s %s -> %d",
        request.method,
        request.url.path,
        response.status_code,
    )
    return response


app.include_router(developer_router)
app.include_router(issues_router)
app.include_router(repo_router)
app.include_router(repo_meta_router)
app.include_router(auth_router)
app.include_router(github_router)
app.include_router(pr_pattern_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
