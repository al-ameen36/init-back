from fastapi import APIRouter, HTTPException

from features.github import get_repo

router = APIRouter(prefix="/repo")


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
