"""
Google token management for Calendar API calls.

Authentication (sign-in) is now handled entirely by Supabase Auth.
This module only handles:
  - Refreshing expired Google Calendar access tokens
  - Storing refreshed tokens back to Supabase

Google CLIENT_ID and CLIENT_SECRET are still needed for token refresh.
Add them to .env under GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.
"""

from datetime import datetime, timedelta, timezone
import os
import requests

from config.settings import settings
from config.google_config import GOOGLE_TOKEN_URI
from config.supabase_client import get_supabase


class GoogleAuthError(Exception):
    pass


def _refresh_access_token(refresh_token: str) -> dict:
    """
    Use the stored Google refresh token to get a new access token.
    Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.
    """
    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise GoogleAuthError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env "
            "to refresh Google Calendar tokens."
        )

    data = {
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }
    resp = requests.post(GOOGLE_TOKEN_URI, data=data, timeout=15)
    if not resp.ok:
        raise GoogleAuthError(
            f"Failed to refresh Google access token — the user may need to "
            f"sign in again to re-grant Calendar access. ({resp.text})"
        )
    return resp.json()


def get_valid_access_token(user: dict) -> str:
    """
    Return a usable Google access token for `user`.
    If the stored token is still valid, return it directly.
    If it has expired, refresh it via the stored refresh token and persist
    the new token back to Supabase.
    """
    expiry_str   = user.get("google_token_expiry")
    access_token = user.get("google_access_token")
    needs_refresh = True

    if access_token:
        if expiry_str:
            try:
                expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                # Treat the token as valid if it expires more than 60 s from now
                if expiry_dt > datetime.now(timezone.utc) + timedelta(seconds=60):
                    needs_refresh = False
            except ValueError:
                needs_refresh = True
        else:
            # Token exists but has no expiry recorded yet — treat as valid
            needs_refresh = False

    if not needs_refresh and access_token:
        return access_token

    refresh_token = user.get("google_refresh_token")
    if not refresh_token:
        if access_token:
            # Fall back to existing access token if no refresh token is stored
            return access_token
        raise GoogleAuthError(
            "Google Calendar is not connected to your account. "
            "Please sign in with Google to grant Calendar access."
        )

    tokens = _refresh_access_token(refresh_token)
    new_access_token = tokens["access_token"]
    expires_in = tokens.get("expires_in", 3599)
    new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    supabase = get_supabase()
    supabase.table("users").update({
        "google_access_token": new_access_token,
        "google_token_expiry": new_expiry,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", user["id"]).execute()

    user["google_access_token"] = new_access_token
    user["google_token_expiry"] = new_expiry
    return new_access_token
