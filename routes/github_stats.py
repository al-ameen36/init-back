from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from features.auth import get_current_user
from features.github import get_profile_stats

logger = logging.getLogger("init")

router = APIRouter(
    prefix="/github",
    tags=["github"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/stats")
async def github_stats(
    username: str = Query(..., description="GitHub username"),
):
    """Live GitHub stats for the skills page. Fetched on demand, not stored."""
    try:
        return get_profile_stats(username)
    except Exception as e:  # noqa: BLE001
        logger.warning("GitHub stats fetch failed for %s: %s", username, e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch GitHub stats: {e}",
        )
