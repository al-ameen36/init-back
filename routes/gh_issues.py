from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from features.gh_issues import get_issues, format_relative_time

router = APIRouter(prefix="/issues")


class IssueSummary(BaseModel):
    number: int
    title: str
    url: str
    labels: list[str]
    comments: int
    opened: str
    repo: str


class IssuesResponse(BaseModel):
    issues: list[IssueSummary]


@router.get("/{repo:path}", response_model=IssuesResponse)
def list_issues_endpoint(repo: str):
    try:
        issues = get_issues(repo, limit=10, state="open")
    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=404, detail=f"Failed to fetch issues for repo:{repo}"
        )

    return IssuesResponse(
        issues=[
            IssueSummary(
                number=issue["number"],
                title=issue["title"],
                url=issue["url"],
                labels=issue["labels"],
                comments=issue.get("comments", 0),
                opened=format_relative_time(issue["created_at"]),
                repo=repo,
            )
            for issue in issues
        ]
    )
