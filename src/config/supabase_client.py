"""
Supabase client singleton.

The backend always uses the service_role key so RLS is bypassed.
The dashboard (Next.js) uses the anon key with JWT — enforced separately.
"""

import asyncio
from functools import lru_cache

from supabase import AClient as AsyncClient, acreate_client

from src.config.settings import settings

# Lock prevents two concurrent coroutines from both calling acreate_client()
_init_lock = asyncio.Lock()

# Module-level async client — initialised once via get_supabase()
_client: AsyncClient | None = None

# Cache: Amazon marketplace_id string (e.g. "A2EUQ1WTGCTBG2") → DB UUID
_marketplace_uuid_cache: dict[str, str] = {}


@lru_cache(maxsize=1)
def _get_supabase_url_and_key() -> tuple[str, str]:
    return settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY


async def get_supabase() -> AsyncClient:
    """
    Return the shared async Supabase client (service_role).
    Initialises on first call; subsequent calls return the cached instance.
    Thread-safe: uses asyncio.Lock to prevent duplicate initialisation.
    """
    global _client
    if _client is not None:
        return _client
    async with _init_lock:
        # Re-check inside lock — another coroutine may have initialised it
        if _client is None:
            url, key = _get_supabase_url_and_key()
            _client = await acreate_client(url, key)
    return _client


async def get_marketplace_uuid(marketplace_code: str) -> str:
    """
    Resolve an Amazon marketplace_id string to its DB UUID.

    Example: "A2EUQ1WTGCTBG2" → "d1bd465d-2c1e-4495-9708-482ca1431786"
    Result is cached after the first DB lookup.
    """
    if marketplace_code in _marketplace_uuid_cache:
        return _marketplace_uuid_cache[marketplace_code]

    db = await get_supabase()
    result = await (
        db.table("marketplaces")
        .select("id")
        .eq("marketplace_id", marketplace_code)
        .single()
        .execute()
    )
    uuid = result.data["id"]
    _marketplace_uuid_cache[marketplace_code] = uuid
    return uuid


async def close_supabase() -> None:
    """Close the Supabase client — call on graceful shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except AttributeError:
            pass  # Some versions of supabase-py don't expose aclose()
        _client = None
