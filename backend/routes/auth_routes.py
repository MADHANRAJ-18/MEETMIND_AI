"""
Auth routes — Supabase Auth handles all OAuth flows.
Token verification uses supabase.auth.get_user() — works for all JWT algorithms.
"""

from flask import Blueprint, request, jsonify, g

from config.supabase_client import get_supabase
from config.settings import settings
from middleware.auth_middleware import token_required

auth_bp = Blueprint("auth_bp", __name__, url_prefix="/api/auth")


@auth_bp.route("/config", methods=["GET"])
def auth_config():
    """
    Public endpoint to provide Supabase client configuration (URL and anon key)
    to the frontend so client credentials don't need to be hardcoded in frontend files.
    """
    return jsonify({
        "supabase_url": settings.SUPABASE_URL,
        "supabase_anon_key": settings.SUPABASE_ANON_KEY,
    })



def _safe_user(user: dict) -> dict:
    return {
        "id":              user["id"],
        "name":            user.get("name"),
        "email":           user.get("email"),
        "profile_picture": user.get("profile_picture"),
        "timezone":        user.get("timezone"),
        "created_at":      user.get("created_at"),
    }


@auth_bp.route("/sync", methods=["POST"])
def sync():
    """
    Called by the frontend immediately after Supabase sign-in.
    Verifies the token via supabase.auth.get_user(), upserts the user
    row and stores the Google Calendar tokens.

    Body: {
      access_token, google_access_token, google_refresh_token,
      name, email, avatar_url, timezone
    }
    """
    body = request.get_json(silent=True) or {}
    access_token = (body.get("access_token") or "").strip()
    if not access_token:
        return jsonify({"error": "access_token is required"}), 400

    supabase = get_supabase()

    # Verify token using Supabase — works for HS256 and RS256 alike
    try:
        response = supabase.auth.get_user(access_token)
        supabase_user = response.user
    except Exception as exc:
        return jsonify({"error": f"Invalid token: {exc}"}), 401

    if not supabase_user:
        return jsonify({"error": "Could not verify token"}), 401

    supabase_user_id = supabase_user.id
    email = (body.get("email") or supabase_user.email or "").lower().strip()

    if not supabase_user_id or not email:
        return jsonify({"error": "Could not extract user identity"}), 400

    import datetime
    now = datetime.datetime.utcnow().isoformat() + "Z"
    record = {
        "id":              supabase_user_id,
        "email":           email,
        "name":            body.get("name") or email.split("@")[0],
        "profile_picture": body.get("avatar_url") or "",
        "timezone":        body.get("timezone") or "UTC",
        "updated_at":      now,
    }

    google_access_token  = (body.get("google_access_token")  or "").strip()
    google_refresh_token = (body.get("google_refresh_token") or "").strip()
    auth_provider = body.get("auth_provider") or "google"
    record["auth_provider"] = auth_provider

    if google_access_token:
        record["google_access_token"] = google_access_token
        # Google access tokens are valid for 1 hour (3600s); store expiry
        record["google_token_expiry"] = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=3300)
        ).isoformat()
    if google_refresh_token:
        record["google_refresh_token"] = google_refresh_token

    try:
        existing = supabase.table("users").select("id, google_refresh_token, google_access_token").eq("id", supabase_user_id).execute()
        if existing.data:
            existing_user = existing.data[0]
            # Preserve existing refresh token if client didn't supply a new one in this sync
            if not google_refresh_token and existing_user.get("google_refresh_token"):
                record["google_refresh_token"] = existing_user["google_refresh_token"]
            if not google_access_token and existing_user.get("google_access_token"):
                record["google_access_token"] = existing_user["google_access_token"]

            result = supabase.table("users").update(record).eq("id", supabase_user_id).execute()
        else:
            record["created_at"] = now
            result = supabase.table("users").insert(record).execute()

        user = result.data[0]
        return jsonify({"user": _safe_user(user)})
    except Exception as exc:
        return jsonify({"error": f"Failed to sync user: {exc}"}), 500


@auth_bp.route("/me", methods=["GET"])
@token_required
def me():
    return jsonify({"user": _safe_user(g.current_user)})


@auth_bp.route("/logout", methods=["POST"])
@token_required
def logout():
    # Supabase handles session invalidation on the client via supabase.auth.signOut()
    return jsonify({"message": "Logged out successfully"})