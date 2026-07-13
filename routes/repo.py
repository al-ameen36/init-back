from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from features.github import get_repo

router = APIRouter(prefix="/repo", dependencies=[Depends(get_current_user)])


@router.get("/{owner}/{name}")
def repo_metadata_endpoint(owner: str, name: str) -> dict:
    try:
        return get_repo(owner, name)
    except Exception as exc:  # noqa: BLE001
        print(exc)
        raise HTTPException(
            status_code=404,
            detail=f"Failed to fetch repository {owner}/{name}",
        )
