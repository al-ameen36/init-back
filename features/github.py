from __future__ import annotations

import base64
import logging
import os
from collections import Counter

import requests

logger = logging.getLogger("init")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
logger.info(
    "GitHub client initialized (authenticated=%s)",
    bool(GITHUB_TOKEN),
)


HEADERS = {
    "Accept": "application/vnd.github+json",
}

if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"


BASE_URL = "https://api.github.com"

REPO_LIMIT = 5


def get_user(username: str) -> dict:
    response = requests.get(
        f"{BASE_URL}/users/{username}",
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_repositories(username: str) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/users/{username}/repos",
        params={
            "per_page": REPO_LIMIT,
            "sort": "updated",
        },
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_repo(owner: str, name: str) -> dict:
    response = requests.get(
        f"{BASE_URL}/repos/{owner}/{name}",
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    repo = response.json()

    return {
        "owner": owner,
        "name": name,
        "full_name": repo.get("full_name", f"{owner}/{name}"),
        "description": repo.get("description"),
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "open_issues_count": repo.get("open_issues_count", 0),
        "html_url": repo.get("html_url"),
        "pushed_at": repo.get("pushed_at"),
        "topics": repo.get("topics", []),
    }


def get_total_stars(
    repositories: list[dict],
) -> int:
    return sum(repo["stargazers_count"] for repo in repositories)


def get_primary_languages(
    repositories: list[dict],
) -> list[str]:

    counter = Counter()

    for repo in repositories:
        language = repo.get("language")

        if language:
            counter[language] += 1

    return [language for language, _ in counter.most_common()]


def get_pull_request_stats(
    username: str,
) -> dict:

    query = f"""
    query {{
      user(login: "{username}") {{
        pullRequests(first: 100) {{
          totalCount
          nodes {{
            merged
          }}
        }}
      }}
    }}
    """

    response = requests.post(
        "https://api.github.com/graphql",
        json={
            "query": query,
        },
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()["data"]["user"]["pullRequests"]

    total = data["totalCount"]

    merged = sum(pr["merged"] for pr in data["nodes"])

    return {
        "total": total,
        "merged": merged,
    }


def get_total_commits(username: str) -> int:
    """Total commit contributions for the user (current contribution year)."""
    query = f"""
    query {{
      user(login: "{username}") {{
        contributionsCollection {{
          totalCommitContributions
        }}
      }}
    }}
    """

    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()["data"]["user"]["contributionsCollection"]
    return data.get("totalCommitContributions", 0)


def get_repository_contents(
    owner: str,
    repo: str,
) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/repos/{owner}/{repo}/contents",
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_profile_stats(username: str) -> dict:
    """Live GitHub stats for the skills page, fetched on demand (no DB
    persistence). Includes 28-day contribution activity, current streak, a
    volume-weighted language proficiency proxy, plus live repo/PR/star/commit
    counts. One GraphQL call covers most of it; repo count comes from REST.
    """
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          totalCommitContributions
          contributionCalendar {
            weeks { contributionDays { contributionCount date } }
          }
        }
        pullRequests(states: MERGED, first: 1) {
          totalCount
        }
        repositories(
          first: 50
          ownerAffiliations: [OWNER]
          orderBy: { field: UPDATED_AT, direction: DESC }
        ) {
          nodes {
            name
            stargazerCount
            languages(first: 10) { edges { size node { name } } }
          }
        }
      }
    }
    """

    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": {"login": username}},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    user = response.json()["data"]["user"]

    # Flatten daily contribution counts (chronological).
    days: list[dict] = []
    for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
        for day in week["contributionDays"]:
            days.append({"date": day["date"], "count": day["contributionCount"]})

    activity = [d["count"] for d in days[-28:]]

    # Current streak: consecutive days with contributions ending today.
    streak = 0
    for day in reversed(days):
        if day["count"] > 0:
            streak += 1
        else:
            break

    # Proficiency proxy: aggregate language bytes across owned repos, then
    # normalize to a 0-100 share of total code volume.
    agg = Counter()
    total_stars = 0
    for repo in user["repositories"]["nodes"]:
        total_stars += repo.get("stargazerCount", 0)
        for edge in repo.get("languages", {}).get("edges", []):
            agg[edge["node"]["name"]] += edge["size"]

    total = sum(agg.values()) or 1
    languages = [
        {
            "name": name,
            "bytes": size,
            "value": round(size / total * 100),
        }
        for name, size in agg.most_common(8)
    ]

    # Live public repo count (includes forks, matching the account total).
    repo_count = get_user(username).get("public_repos", 0)

    return {
        "activity": activity,
        "streak": streak,
        "languages": languages,
        "repos": repo_count,
        "merged_prs": user["pullRequests"]["totalCount"],
        "total_stars": total_stars,
        "total_commits": user["contributionsCollection"]["totalCommitContributions"],
    }


def download_file(
    owner: str,
    repo: str,
    path: str,
) -> str:
    response = requests.get(
        f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}",
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    content = data.get("content", "")
    encoding = data.get("encoding", "base64")

    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8", errors="replace")

    return content
