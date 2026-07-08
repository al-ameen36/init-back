from fastapi import FastAPI
from routes import issues_router, repo_router

app = FastAPI(title="Codebase Analyzer API")

# Routes
app.include_router(issues_router)
app.include_router(repo_router)
