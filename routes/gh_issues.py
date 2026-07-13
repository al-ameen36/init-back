from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from features.gh_issues import get_issues, format_relative_time

from app.auth import get_current_user

router = APIRouter(prefix="/issues", dependencies=[Depends(get_current_user)])


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
def list_issues_endpoint(repo: str, limit: int = 5):
    try:
        issues = get_issues(repo, limit=limit, state="open")
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
