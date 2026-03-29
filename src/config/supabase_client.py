"""
Supabase client singleton.

The backend always uses the service_role key so RLS is bypassed.
The dashboard (Next.js) uses the anon key with JWT — enforced separately.
"""

from functools import lru_cache

from supabase import AClient as AsyncClient, acreate_client

from src.config.settings import settings


@lru_cache(maxsize=1)
def _get_supabase_url_and_key() -> tuple[str, str]:
    return settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY


# Module-level async client — initialised once via get_supabase()
_client: AsyncClient | None = None


async def get_supabase() -> AsyncClient:
    """
    Return the shared async Supabase client (service_role).
    Initialises on first call; subsequent calls return the cached instance.
    """
    global _client
    if _client is None:
        url, key = _get_supabase_url_and_key()
        _client = await acreate_client(url, key)
    return _client


async def close_supabase() -> None:
    """Close the Supabase client — call on graceful shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
