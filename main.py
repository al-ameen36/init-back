from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from llm import analyze_issue, score_files, generate_developer_guide
from graph_sitter import Codebase
from gh_issues import get_issue_by_number, format_issue
from search import perform_search

app = FastAPI(title="Codebase Analyzer API")


class AnalyzeRequest(BaseModel):
    repo: str
    issue_number: int


class ScoredFile(BaseModel):
    file: str
    confidence_score: int
    reasoning: str


class AnalyzeResponse(BaseModel):
    scored_files: list[ScoredFile]
    developer_guide: str


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest):
    try:
        # 1. Fetch issue
        selected_issue = get_issue_by_number(req.repo, req.issue_number)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Failed to fetch issue: {e}")

    issue_text = format_issue(selected_issue)

    # 2. Get search queries from LLM
    queries = analyze_issue(issue_text)

    # 3. Initialize codebase and search
    try:
        codebase = Codebase.from_repo(req.repo)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to initialize codebase: {e}"
        )

    file_matches = perform_search(codebase, queries)

    scored_files = []
    if file_matches:
        # 4. Score files based on matches
        scored_files = score_files(issue_text, file_matches)
        # 5. Sort by confidence score descending
        scored_files.sort(key=lambda x: x.get("confidence_score", 0), reverse=True)

    # 6. Generate Developer Guide
    developer_guide = generate_developer_guide(issue_text, scored_files)

    return AnalyzeResponse(
        scored_files=[ScoredFile(**sf) for sf in scored_files],
        developer_guide=developer_guide,
    )
