"""
Supabase JWT authentication for the FastAPI backend.

Verifies the Supabase-issued access token (asymmetric RS256/ES256) against the
project's JWKS endpoint. Protect any route by adding
``dependencies=[Depends(get_current_user)]`` to its ``APIRouter``.
"""

import logging
import os
import time

import httpx
from fastapi import HTTPException, Request
from jose import jwt

logger = logging.getLogger(__name__)

ACCESS_TOKEN_COOKIE = "sb-access-token"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL") or (
    f"{SUPABASE_URL}/auth/v1/keys" if SUPABASE_URL else None
)

# Cache the JWKS in memory; Supabase rotates signing keys infrequently.
_JWKS: dict = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL = 3600.0


async def _get_jwks() -> dict:
    now = time.time()
    if _JWKS["keys"] is not None and now - _JWKS["fetched_at"] < _JWKS_TTL:
        return _JWKS["keys"]
    if not SUPABASE_JWKS_URL:
        raise RuntimeError("SUPABASE_JWKS_URL not configured")
    async with httpx.AsyncClient() as client:
        resp = await client.get(SUPABASE_JWKS_URL, timeout=5.0)
    resp.raise_for_status()
    jwks = resp.json()
    _JWKS["keys"] = jwks
    _JWKS["fetched_at"] = now
    return jwks


async def verify_supabase_token(token: str) -> dict | None:
    """Verify a Supabase JWT against the project JWKS. Returns the payload or None."""
    try:
        jwks = await _get_jwks()
        return jwt.decode(
            token,
            jwks,
            algorithms=["RS256", "ES256"],
            options={"verify_aud": False},
        )
    except Exception as e:  # noqa: BLE001 - any verification failure => unauthenticated
        logger.warning("JWT verification failed: %s: %s", type(e).__name__, e)
        return None


def _extract_token(request: Request) -> str | None:
    """Pull the Supabase access token from the ``Authorization`` header or the
    httpOnly session cookie (used by browser ``EventSource`` / fetch with
    credentials, which cannot set custom headers)."""
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer ") :]
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: require a valid Supabase token.

    Reads the token from the ``Authorization`` header or the
    ``sb-access-token`` cookie. Raises ``401`` when missing or invalid.
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid token",
        )

    payload = await verify_supabase_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_metadata = payload.get("user_metadata", {}) or {}
    return {
        "id": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("role", "authenticated"),
        "github_username": (
            user_metadata.get("github_username")
            or user_metadata.get("user_name")
            or user_metadata.get("preferred_username")
            or payload.get("preferred_username")
        ),
    }
