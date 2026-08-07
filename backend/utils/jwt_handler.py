"""
Deprecated — JWT generation is now handled by Supabase Auth.
This file is kept as a stub to avoid import errors in any remaining references.
All token verification now happens in middleware/auth_middleware.py using
the SUPABASE_JWT_SECRET.
"""

# If any legacy code still imports these, they will raise informative errors.

def generate_token(*args, **kwargs):
    raise RuntimeError(
        "generate_token is deprecated. Supabase Auth now issues JWTs. "
        "Use supabase.auth.signInWithOAuth on the frontend."
    )

def decode_token(*args, **kwargs):
    raise RuntimeError(
        "decode_token is deprecated. Use auth_middleware.token_required "
        "which now verifies Supabase JWTs directly."
    )
