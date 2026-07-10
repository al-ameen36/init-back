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
