import logging

from fastapi import APIRouter, Depends, HTTPException

from features.auth import get_current_user
from features.github import get_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repo", dependencies=[Depends(get_current_user)])


@router.get("/{owner}/{name}")
def repo_metadata_endpoint(owner: str, name: str) -> dict:
    try:
        return get_repo(owner, name)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to fetch repository %s/%s: %s", owner, name, exc, exc_info=True
        )
        raise HTTPException(
            status_code=404,
            detail=f"Failed to fetch repository {owner}/{name}",
        )
