"""
Supabase client initialization.
"""

import os
from supabase import create_async_client, AsyncClient

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

supabase: AsyncClient | None = None


async def init_supabase() -> None:
    """Create the async Supabase client (awaited once at app startup)."""
    global supabase
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY")
    try:
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    except Exception as e:
        raise RuntimeError(f"Failed to init Supabase client: {e}")


def get_supabase() -> AsyncClient | None:
    """Return the already-initialized async client (or None)."""
    return supabase
