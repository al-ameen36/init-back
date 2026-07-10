from fastapi import APIRouter, Depends
from app.auth import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)) -> dict:
    return user
