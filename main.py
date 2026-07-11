import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from supabase_client import init_supabase
from routes import (
    developer_router,
    issues_router,
    repo_router,
    repo_meta_router,
    auth_router,
    github_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("init")

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
