from gh_issues import format_relative_time
from llm import generate_investigation_guide, score_files
from search import perform_search
from graph_sitter import Codebase
from llm import analyze_issue
from gh_issues import format_issue, get_issue_by_number
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/analyze")


class AnalyzeRequest(BaseModel):
    repo: str
    issue_number: int


class ScoredFile(BaseModel):
    file: str
    confidence_score: int
    reasoning: str


class InvestigationGuide(BaseModel):
    difficulty: str
    comments: int
    opened: str
    summary: str
    relevant_files: list[str]
    investigation_path: list[str]


class AnalyzeResponse(BaseModel):
    scored_files: list[ScoredFile]
    guide: InvestigationGuide


@router.post("/", response_model=AnalyzeResponse)
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

    # 6. Generate structured investigation guide
    guide_data = generate_investigation_guide(issue_text, scored_files)

    guide = InvestigationGuide(
        difficulty=guide_data.get("difficulty", "Medium"),
        comments=selected_issue.get("comments", 0),
        opened=format_relative_time(selected_issue["created_at"]),
        summary=guide_data.get("summary", ""),
        relevant_files=guide_data.get("relevant_files", []),
        investigation_path=guide_data.get("investigation_path", []),
    )

    return AnalyzeResponse(
        scored_files=[ScoredFile(**sf) for sf in scored_files],
        guide=guide,
    )
