"""Fetch open issues from a GitHub repository using the REST API."""

import requests


GITHUB_API = "https://api.github.com"


def get_issues(repo: str, limit: int = 10, state: str = "open") -> list[dict]:
    """Fetch issues from a GitHub repo.

    Args:
        repo: Owner/name format, e.g. "psf/requests".
        limit: Max number of issues to return.
        state: Issue state filter ("open", "closed", "all").

    Returns:
        A list of issue dicts with keys: number, title, body, url, labels.
    """
    url = f"{GITHUB_API}/repos/{repo}/issues"
    params = {
        "state": state,
        "per_page": limit,
        "sort": "created",
        "direction": "desc",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()

    issues = []
    for item in resp.json():
        # The /issues endpoint also returns pull requests; skip those
        if "pull_request" in item:
            continue
        issues.append(
            {
                "number": item["number"],
                "title": item["title"],
                "body": item.get("body", "") or "",
                "url": item["html_url"],
                "labels": [label["name"] for label in item.get("labels", [])],
            }
        )

    return issues


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
