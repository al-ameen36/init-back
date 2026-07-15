import hashlib
import json
import logging
import os
from typing import Any, cast

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import requests
from supabase_client import get_supabase

from features.llm import generate_investigation_guide, score_files, analyze_issue
from features.code_graph import ensure_graph, graph_search, list_file_paths
from features.gh_issues import (
    format_relative_time,
    format_issue,
    get_issue_by_number,
    get_repo_metadata,
)

from features.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analyze", dependencies=[Depends(get_current_user)])


class BatchAnalyzeRequest(BaseModel):
    repo: str
    issue_numbers: list[int]
    developer_profile: dict | None = None
    force: bool = False


class ScoredFile(BaseModel):
    file: str
    confidence_score: int
    reasoning: str
    github_url: str | None = None


class InvestigationGuide(BaseModel):
    difficulty: str
    comments: int
    opened: str
    summary: str
    relevant_files: list[str]
    investigation_path: list[str]
    required_skills: list[str] = []


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
    commit_sha: str | None = None


def _default_branch_sha(repo: str) -> str | None:
    """Latest commit SHA of the repo's default branch (for stable GitHub links)."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/commits?per_page=1", timeout=15
        )
        if resp.status_code == 200 and resp.json():
            return resp.json()[0].get("sha")
    except Exception:  # noqa: BLE001
        pass
    return None


def _build_path_index(paths: set[str]) -> dict[str, str]:
    """Index repo paths for fuzzy lookup so slightly-off paths emitted by the
    LLM (wrong case, singular/plural) still resolve to a real file."""
    index: dict[str, str] = {}
    for p in paths:
        index[p.lower()] = p
    return index


def _resolve_path(index: dict[str, str], path: str) -> str | None:
    """Return the real repo path for ``path`` or ``None`` if it can't be
    matched. Tries exact, case-insensitive, then singular/plural variants on
    the filename stem (so ``parser.js`` resolves to ``parsers.js``)."""
    if not path:
        return None
    key = path.lower()
    if key in index:
        return index[key]
    stem, ext = os.path.splitext(key)
    if stem + "s" + ext in index:
        return index[stem + "s" + ext]
    if stem.endswith("s") and stem[:-1] + ext in index:
        return index[stem[:-1] + ext]
    return None


def analyze_one(repo, issue_number, developer_profile, store, repo_meta, commit_sha):
    selected_issue = get_issue_by_number(repo, issue_number)
    issue_text = format_issue(selected_issue)

    # Search queries from LLM
    queries = analyze_issue(issue_text)

    # Serve search from the persistent graph store (no live parser needed).
    file_matches = graph_search(store, repo, queries)

    scored_files = []
    if file_matches:
        scored_files = score_files(issue_text, file_matches)
        scored_files.sort(key=lambda x: x.get("confidence_score", 0), reverse=True)

    # Only keep files that actually exist in the analyzed tree — the graph or
    # the LLM can surface paths that don't exist on GitHub (wrong case,
    # singular/plural, or a file moved since this commit), which would 404.
    existing: set[str] = list_file_paths(store, repo)
    path_index = _build_path_index(existing)

    resolved_scored: list[tuple[str, dict]] = []
    for sf in scored_files:
        real = _resolve_path(path_index, sf["file"])
        if real is None:
            continue
        # Several slightly-off names can resolve to the same real file; keep
        # only the highest-scoring entry per resolved path.
        dup = next((item for item in resolved_scored if item[0] == real), None)
        if dup is None:
            resolved_scored.append((real, sf))
        elif sf.get("confidence_score", 0) > dup[1].get("confidence_score", 0):
            resolved_scored.remove(dup)
            resolved_scored.append((real, sf))
    scored_files = [sf for _, sf in resolved_scored]

    guide_data = generate_investigation_guide(issue_text, scored_files)

    # Build clickable GitHub URLs pinned to the analyzed commit so they never
    # 404 against a later ref (e.g. a file moved on the default branch).
    def file_url(path: str) -> str | None:
        if not commit_sha:
            return None
        return f"https://github.com/{repo}/blob/{commit_sha}/{path}"

    scored_files_out = [
        ScoredFile(
            file=real,
            confidence_score=sf["confidence_score"],
            reasoning=sf["reasoning"],
            github_url=file_url(real),
        )
        for real, sf in resolved_scored
    ]

    # Filter the LLM's free-text relevant_files to those that resolve in the
    # codebase, so the frontend doesn't link to nonexistent paths.
    relevant_files = [
        rf
        for raw in guide_data.get("relevant_files", [])
        if (rf := _resolve_path(path_index, raw)) is not None
    ]

    guide = InvestigationGuide(
        difficulty=guide_data.get("difficulty", "Medium"),
        comments=selected_issue.get("comments", 0),
        opened=format_relative_time(selected_issue["created_at"]),
        summary=guide_data.get("summary", ""),
        relevant_files=relevant_files,
        investigation_path=guide_data.get("investigation_path", []),
        required_skills=guide_data.get("required_skills", []),
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
        repo=repo,
        language=repo_meta["language"],
        matchScore=match_score,
        matchReasons=match_reasons,
        related=[],
        scored_files=scored_files_out,
        guide=guide,
        commit_sha=commit_sha,
    )


def profile_key_of(profile: dict | None) -> str:
    if not profile:
        return "anon"
    payload = json.dumps(profile, sort_keys=True, default=str)
    return "p_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _row_to_response(row: dict[str, Any]) -> AnalyzeResponse:
    return AnalyzeResponse(
        number=row["issue_number"],
        title=row.get("title") or "",
        repo=row["repo"],
        language=row.get("language") or "",
        matchScore=row.get("match_score") or 0,
        matchReasons=row.get("match_reasons") or [],
        related=[],
        scored_files=[ScoredFile(**sf) for sf in (row.get("scored_files") or [])],
        guide=InvestigationGuide(**(row.get("guide") or {})),
    )


async def _save_cached(repo: str, num: int, pk: str, r: AnalyzeResponse) -> None:
    supabase = get_supabase()
    if supabase is None:
        return
    row = {
        "repo": repo,
        "issue_number": num,
        "profile_key": pk,
        "title": r.title,
        "language": r.language,
        "match_score": r.matchScore,
        "match_reasons": r.matchReasons,
        "scored_files": [sf.model_dump() for sf in r.scored_files],
        "guide": r.guide.model_dump(),
    }
    try:
        await (
            supabase.table("analysis")
            .upsert(row, on_conflict="repo,issue_number,profile_key")
            .execute()
        )
    except Exception as e:
        logger.warning("Failed to cache analysis: %s", e)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/")
async def analyze_endpoint(req: BatchAnalyzeRequest) -> StreamingResponse:
    pk = profile_key_of(req.developer_profile)
    supabase = get_supabase()

    # 1. Load any cached analysis for this repo + profile.
    cached_map: dict[int, dict[str, Any]] = {}
    if not req.force and supabase is not None:
        try:
            res = (
                await supabase.table("analysis")
                .select("*")
                .eq("repo", req.repo)
                .eq("profile_key", pk)
                .in_("issue_number", req.issue_numbers)
                .execute()
            )
            for raw in res.data or []:
                row = cast("dict[str, Any]", raw)
                cached_map[int(row["issue_number"])] = row
        except Exception as e:
            logger.warning("Analysis cache read failed: %s", e)

    to_compute = [n for n in req.issue_numbers if n not in cached_map]

    async def event_gen():
        # Emit cached results immediately so they paint without waiting.
        for row in cached_map.values():
            yield _sse(
                {"type": "result", "analysis": _row_to_response(row).model_dump()}
            )

        if not to_compute:
            return

        # 2. Compute only the uncached issues, reusing one codebase build,
        #    streaming each result the moment it finishes.
        yield _sse(
            {"type": "status", "stage": "building", "message": "Building codebase…"}
        )

        try:
            store = await run_in_threadpool(ensure_graph, req.repo)
        except Exception as e:
            logger.error("Failed to build code graph: %s", e, exc_info=True)
            yield _sse(
                {
                    "type": "error",
                    "stage": "build",
                    "message": f"Failed to build code graph: {e}",
                }
            )
            return

        # Pin every generated GitHub link to the exact commit we analyzed,
        # so links never 404 against a later ref.
        commit_sha = await run_in_threadpool(_default_branch_sha, req.repo)

        try:
            repo_meta = await run_in_threadpool(get_repo_metadata, req.repo)
        except Exception as e:
            logger.error("Failed to fetch repo: %s", e, exc_info=True)
            yield _sse(
                {
                    "type": "error",
                    "stage": "build",
                    "message": f"Failed to fetch repo: {e}",
                }
            )
            return

        for num in to_compute:
            try:
                result = await run_in_threadpool(
                    analyze_one,
                    req.repo,
                    num,
                    req.developer_profile,
                    store,
                    repo_meta,
                    commit_sha,
                )
                await _save_cached(req.repo, num, pk, result)
                yield _sse({"type": "result", "analysis": result.model_dump()})
            except Exception as e:
                logger.error("Failed to analyze issue %s: %s", num, e, exc_info=True)
                yield _sse({"type": "error", "number": num, "message": str(e)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
