"""
Supabase JWT authentication module for FastAPI backend.
"""

import os
from typing import Optional
from fastapi import HTTPException, Request
from jose import jwt
import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL") or (
    f"{SUPABASE_URL}/auth/v1/keys" if SUPABASE_URL else None
)


async def verify_supabase_token(token: str) -> Optional[dict]:
    """Verify a Supabase JWT token using the JWKS endpoint."""
    if not SUPABASE_JWKS_URL:
        raise RuntimeError("SUPABASE_JWKS_URL not configured")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(SUPABASE_JWKS_URL, timeout=5.0)
        resp.raise_for_status()
        jwks = resp.json()

        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256", "HS256", "ES256"],
            options={"verify_aud": False},
        )
        return payload
    except Exception as e:
        print(f"JWT verification failed: {type(e).__name__}: {e}")
        return None


async def get_current_user(request: Request) -> dict:
    """Get current user from validated JWT token."""
    authorization = request.headers.get("Authorization", "")

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    token = authorization[7:]

    payload = await verify_supabase_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_metadata = payload.get("user_metadata", {})
    return {
        "id": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("role", "user"),
        "github_username": (
            user_metadata.get("github_username")
            or user_metadata.get("user_name")
            or user_metadata.get("preferred_username")
        ),
    }
