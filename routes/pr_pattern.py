from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from features.auth import get_current_user
from features.github import BASE_URL, GITHUB_TOKEN, HEADERS
from pr_pattern_analyzer import EvidenceCollector, build_playbook
from pr_pattern_analyzer.models import PRFact

logger = logging.getLogger("init")

pr_pattern_router = APIRouter(
    prefix="/pr-pattern", dependencies=[Depends(get_current_user)]
)

DEFAULT_LIMIT = 5
MAX_LIMIT = 20

CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\(.+\))?!?:",
    re.IGNORECASE,
)

ISSUE_RE = re.compile(r"(?:closes|fixes|resolves)\s+#\d+", re.IGNORECASE)


class RepoAnalyzeRequest(BaseModel):
    repo: str
    limit: int = DEFAULT_LIMIT


class PrAnalyzeRequest(BaseModel):
    repo: str
    pr_number: int


def _split(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not name:
        raise HTTPException(status_code=400, detail="repo must be 'owner/name'")
    return owner, name


def _merged_prs(owner: str, name: str, limit: int) -> list[dict]:
    """Fetch recent merged PRs with metadata from GitHub."""
    resp = requests.get(
        f"{BASE_URL}/repos/{owner}/{name}/pulls",
        params={
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": max(limit * 3, 30),
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    prs: list[dict] = []
    for pr in resp.json():
        if not pr.get("merged_at") or not pr.get("merge_commit_sha"):
            continue
        prs.append(
            {
                "sha": pr["merge_commit_sha"],
                "number": pr["number"],
                "title": pr.get("title", ""),
                "body": (pr.get("body") or "")[:500],
                "labels": [lb["name"] for lb in pr.get("labels", [])],
                "created_at": pr.get("created_at", ""),
                "merged_at": pr.get("merged_at", ""),
            }
        )
        if len(prs) >= limit:
            break
    return prs


def _fetch_review_count(owner: str, name: str, pr_number: int) -> int:
    """Count reviews on a PR (returns 0 on failure)."""
    try:
        resp = requests.get(
            f"{BASE_URL}/repos/{owner}/{name}/pulls/{pr_number}/reviews",
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            return len(resp.json())
    except Exception:  # noqa: BLE001
        pass
    return 0


def _hours_to_merge(created_at: str, merged_at: str) -> float:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        merged = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        return max(0.0, (merged - created).total_seconds() / 3600)
    except Exception:  # noqa: BLE001
        return 0.0


def _scope(files_changed: int) -> str:
    if files_changed <= 3:
        return "localized"
    if files_changed <= 10:
        return "moderate"
    return "broad"


def _build_fact(pr_meta: dict, evidence, review_rounds: int) -> PRFact:
    """Deterministic: merge git evidence + PR metadata into a PRFact."""
    ev = evidence
    return PRFact(
        pr_number=pr_meta["number"],
        title=pr_meta["title"],
        body=pr_meta["body"],
        labels=pr_meta["labels"],
        sha=pr_meta["sha"],
        files_changed=ev.metadata.files_changed,
        insertions=ev.metadata.insertions,
        deletions=ev.metadata.deletions,
        source_files=ev.source_files,
        test_files=ev.test_files,
        doc_files=ev.doc_files,
        has_tests=bool(ev.test_files),
        has_docs=bool(ev.doc_files),
        has_changelog=ev.changelog_changed,
        has_readme=ev.readme_changed,
        scope=_scope(ev.metadata.files_changed),
        time_to_merge_hours=_hours_to_merge(
            pr_meta["created_at"], pr_meta["merged_at"]
        ),
        review_rounds=review_rounds,
        linked_issue=bool(ISSUE_RE.search(pr_meta["body"])),
        title_conventional=bool(CONVENTIONAL_RE.match(pr_meta["title"])),
    )


def _clone(owner: str, name: str) -> str:
    tmp = tempfile.mkdtemp(prefix="prpat_")
    auth = f"{GITHUB_TOKEN}@" if GITHUB_TOKEN else ""
    url = f"https://{auth}github.com/{owner}/{name}.git"
    try:
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", url, tmp],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"clone failed: {exc}") from exc
    return tmp


@pr_pattern_router.post("/analyze")
def analyze_repository(request: RepoAnalyzeRequest) -> dict:
    """Analyze a repo's recent merged PRs into a ContributorPlaybook."""
    owner, name = _split(request.repo)
    limit = max(1, min(request.limit, MAX_LIMIT))
    try:
        pr_metas = _merged_prs(owner, name, limit)
        if not pr_metas:
            raise HTTPException(status_code=404, detail="No merged PRs found")
        tmp = _clone(owner, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("pr-pattern prep failed for %s/%s", owner, name)
        raise HTTPException(status_code=502, detail=f"Failed to prepare repo: {exc}")

    try:
        collector = EvidenceCollector(tmp)
        facts: list[PRFact] = []
        for pr_meta in pr_metas:
            try:
                evidence = collector.collect(pr_meta["sha"])
                reviews = _fetch_review_count(owner, name, pr_meta["number"])
                facts.append(_build_fact(pr_meta, evidence, reviews))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PR #%d evidence collection failed: %s", pr_meta["number"], exc
                )
        if not facts:
            raise HTTPException(
                status_code=502, detail="All PR evidence collection failed"
            )
        playbook = build_playbook(facts, request.repo)
        return playbook.model_dump()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pr_pattern_router.post("/pr")
def analyze_pull_request(request: PrAnalyzeRequest) -> dict:
    """Analyze a single merged PR into a ContributorPlaybook."""
    owner, name = _split(request.repo)
    try:
        pr = requests.get(
            f"{BASE_URL}/repos/{owner}/{name}/pulls/{request.pr_number}",
            headers=HEADERS,
            timeout=30,
        )
        pr.raise_for_status()
        pr_data = pr.json()
        sha = pr_data.get("merge_commit_sha") or pr_data.get("head", {}).get("sha")
        if not sha:
            raise HTTPException(status_code=404, detail="PR has no merge commit")
        tmp = _clone(owner, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "pr-pattern prep failed for %s/%s#%d", owner, name, request.pr_number
        )
        raise HTTPException(status_code=502, detail=f"Failed to prepare repo: {exc}")

    try:
        collector = EvidenceCollector(tmp)
        evidence = collector.collect(sha)
        reviews = _fetch_review_count(owner, name, request.pr_number)
        created_at = pr_data.get("created_at", "")
        merged_at = pr_data.get("merged_at", "")
        body_preview = (pr_data.get("body") or "")[:500]
        labels = [lb["name"] for lb in pr_data.get("labels", [])]

        fact = PRFact(
            pr_number=request.pr_number,
            title=pr_data.get("title", ""),
            body=body_preview,
            labels=labels,
            sha=sha,
            files_changed=evidence.metadata.files_changed,
            insertions=evidence.metadata.insertions,
            deletions=evidence.metadata.deletions,
            source_files=evidence.source_files,
            test_files=evidence.test_files,
            doc_files=evidence.doc_files,
            has_tests=bool(evidence.test_files),
            has_docs=bool(evidence.doc_files),
            has_changelog=evidence.changelog_changed,
            has_readme=evidence.readme_changed,
            scope=_scope(evidence.metadata.files_changed),
            time_to_merge_hours=_hours_to_merge(created_at, merged_at),
            review_rounds=reviews,
            linked_issue=bool(ISSUE_RE.search(body_preview)),
            title_conventional=bool(CONVENTIONAL_RE.match(pr_data.get("title", ""))),
        )
        playbook = build_playbook([fact], request.repo)
        return playbook.model_dump()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
