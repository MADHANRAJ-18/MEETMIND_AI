"""
Supabase client singleton.

Use a service-role key (not the anon key) so the Flask backend can read
and write the `users` and `meetings` tables without being blocked by
Row Level Security policies.
"""

from supabase import create_client, Client
from config.settings import settings

_supabase_client: Client | None = None


def get_supabase() -> Client:
    """Return a shared Supabase client, creating it on first use."""
    global _supabase_client
    if _supabase_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in environment variables."
            )
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _supabase_client
