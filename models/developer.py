from __future__ import annotations

from pydantic import BaseModel


class AnalysisEvent(BaseModel):
    step: str
    status: str = "completed"
    data: dict = {}


class AnalyzeDeveloperRequest(BaseModel):
    username: str


class AnalyzeDeveloperResponse(BaseModel):
    job_id: str
