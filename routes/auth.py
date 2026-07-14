from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.auth import ACCESS_TOKEN_COOKIE, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class SessionRequest(BaseModel):
    access_token: str


@router.post("/session")
async def set_session(payload: SessionRequest, response: Response) -> dict:
    """Set the httpOnly session cookie so the browser can authenticate SSE
    streams (EventSource) without sending a header."""
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        payload.access_token,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)) -> dict:
    return user
