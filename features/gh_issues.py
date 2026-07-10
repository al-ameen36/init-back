"""Fetch open issues from a GitHub repository using the REST API."""

from datetime import datetime, timezone

import requests

from features.github import HEADERS


GITHUB_API = "https://api.github.com"


def get_issues(repo: str, limit: int = 5, state: str = "open") -> list[dict]:
    """Fetch issues from a GitHub repo.

    Uses the Search API with ``is:issue`` so pull requests are excluded at the
    source. The plain ``/repos/{repo}/issues`` endpoint interleaves PRs, and a
    PR-heavy repo (e.g. Kong/insomnia) can return an all-PR page that looks
    empty once those are filtered out.

    Args:
        repo: Owner/name format, e.g. "psf/requests".
        limit: Max number of issues to return.
        state: Issue state filter ("open", "closed", "all").

    Returns:
        A list of issue dicts with keys: number, title, body, url, labels,
        comments, created_at.
    """
    url = f"{GITHUB_API}/search/issues"
    query = f"repo:{repo} is:issue state:{state}"
    params = {
        "q": query,
        "per_page": limit,
        "sort": "created",
        "order": "desc",
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    issues = []
    for item in resp.json().get("items", []):
        # Defensive: the is:issue qualifier already excludes PRs, but skip
        # anything that sneaks through.
        if "pull_request" in item:
            continue
        issues.append(
            {
                "number": item["number"],
                "title": item["title"],
                "body": item.get("body", "") or "",
                "url": item["html_url"],
                "labels": [label["name"] for label in item.get("labels", [])],
                "comments": item.get("comments", 0),
                "created_at": item["created_at"],
            }
        )

    return issues


def get_issue_by_number(repo: str, issue_number: int) -> dict:
    """Fetch a specific issue by number."""
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    item = resp.json()

    return {
        "number": item["number"],
        "title": item["title"],
        "body": item.get("body", "") or "",
        "url": item["html_url"],
        "labels": [label["name"] for label in item.get("labels", [])],
        "comments": item.get("comments", 0),
        "created_at": item["created_at"],
    }


def format_issue(issue: dict) -> str:
    """Format an issue dict into a text block suitable for LLM analysis."""
    lines = [
        f"Issue #{issue['number']}: {issue['title']}",
        f"URL: {issue['url']}",
    ]
    if issue["labels"]:
        lines.append(f"Labels: {', '.join(issue['labels'])}")
    lines.append("")
    lines.append(issue["body"])
    return "\n".join(lines)


def format_relative_time(iso_timestamp: str) -> str:
    """Format an ISO 8601 timestamp as a short relative time string, e.g. "3d ago"."""
    created = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    seconds = int((now - created).total_seconds())

    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = months // 12
    return f"{years}y ago"


def get_repo_metadata(repo: str) -> dict:
    """Fetch repo-level metadata not available on individual issues."""
    url = f"{GITHUB_API}/repos/{repo}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    item = resp.json()

    return {
        "stars": item.get("stargazers_count", 0),
        "language": item.get("language") or "Unknown",
    }
