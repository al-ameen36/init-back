import hashlib
import json
from typing import Any, cast

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, HTTPException
from graph_sitter import Codebase
from pydantic import BaseModel
from supabase_client import get_supabase

from features.llm import generate_investigation_guide, score_files, analyze_issue
from features.search import perform_search
from features.gh_issues import (
    format_relative_time,
    format_issue,
    get_issue_by_number,
    get_repo_metadata,
)

router = APIRouter(prefix="/analyze")


class BatchAnalyzeRequest(BaseModel):
    repo: str
    issue_numbers: list[int]
    developer_profile: dict | None = None
    force: bool = False


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


def analyze_one(repo, issue_number, developer_profile, codebase, repo_meta):
    selected_issue = get_issue_by_number(repo, issue_number)
    issue_text = format_issue(selected_issue)

    # Search queries from LLM
    queries = analyze_issue(issue_text)

    # Reuse the already-built codebase for every issue in the batch
    file_matches = perform_search(codebase, queries)

    scored_files = []
    if file_matches:
        scored_files = score_files(issue_text, file_matches)
        scored_files.sort(key=lambda x: x.get("confidence_score", 0), reverse=True)

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
        repo=repo,
        language=repo_meta["language"],
        matchScore=match_score,
        matchReasons=match_reasons,
        related=[],
        scored_files=[ScoredFile(**sf) for sf in scored_files],
        guide=guide,
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
            supabase.table("analyses")
            .upsert(row, on_conflict="repo,issue_number,profile_key")
            .execute()
        )
    except Exception as e:
        print("Failed to cache analysis:", e)


@router.post("/", response_model=list[AnalyzeResponse])
async def analyze_endpoint(req: BatchAnalyzeRequest):
    pk = profile_key_of(req.developer_profile)
    supabase = get_supabase()

    # 1. Load any cached analyses for this repo + profile.
    cached_map: dict[int, dict[str, Any]] = {}
    if not req.force and supabase is not None:
        try:
            res = (
                await supabase.table("analyses")
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
            print("Analysis cache read failed:", e)

    to_compute = [n for n in req.issue_numbers if n not in cached_map]

    # 2. Compute only the issues that aren't cached, reusing one codebase build.
    computed: list[AnalyzeResponse] = []
    if to_compute:
        try:
            codebase = await run_in_threadpool(Codebase.from_repo, req.repo)
        except Exception as e:
            print(e)
            raise HTTPException(
                status_code=500, detail=f"Failed to initialize codebase: {e}"
            )

        try:
            repo_meta = await run_in_threadpool(get_repo_metadata, req.repo)
        except Exception as e:
            print(e)
            raise HTTPException(status_code=404, detail=f"Failed to fetch repo: {e}")

        for num in to_compute:
            try:
                result = await run_in_threadpool(
                    analyze_one,
                    req.repo,
                    num,
                    req.developer_profile,
                    codebase,
                    repo_meta,
                )
                computed.append(result)
                await _save_cached(req.repo, num, pk, result)
            except Exception as e:
                print(f"Failed to analyze issue #{num}: {e}")
                continue

    # 3. Return cached + freshly computed results.
    cached = [_row_to_response(row) for row in cached_map.values()]
    return cached + computed
