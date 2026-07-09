from graph_sitter import Codebase
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from features.llm import generate_investigation_guide, score_files, analyze_issue
from features.search import perform_search
from features.gh_issues import (
    format_relative_time,
    format_issue,
    get_issue_by_number,
    get_repo_metadata,
)

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
    number: int
    title: str
    repo: str
    language: str
    matchScore: int
    matchReasons: list[str]
    related: list[str]
    scored_files: list[ScoredFile]
    guide: InvestigationGuide


@router.post("/", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest):
    try:
        # 1. Fetch issue
        selected_issue = get_issue_by_number(req.repo, req.issue_number)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=404, detail=f"Failed to fetch issue: {e}")

    try:
        repo_meta = get_repo_metadata(req.repo)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=404, detail=f"Failed to fetch repo: {e}")

    issue_text = format_issue(selected_issue)

    # 2. Get search queries from LLM
    try:
        queries = analyze_issue(issue_text)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=404, detail=f"Failed to analyze issue: {e}")

    # 3. Initialize codebase and search
    try:
        codebase = Codebase.from_repo(req.repo)
    except Exception as e:
        print(e)
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

    match_score = scored_files[0]["confidence_score"] if scored_files else 0
    # Dedup reasoning strings — score_files can emit the same reasoning
    # multiple times across snippet matches within one file
    match_reasons = list(
        dict.fromkeys(
            sf["reasoning"] for sf in scored_files if sf.get("confidence_score", 0) > 0
        )
    )

    return AnalyzeResponse(
        number=selected_issue["number"],
        title=selected_issue["title"],
        repo=req.repo,
        language=repo_meta["language"],
        matchScore=match_score,
        matchReasons=match_reasons,
        related=[],
        scored_files=[ScoredFile(**sf) for sf in scored_files],
        guide=guide,
    )
