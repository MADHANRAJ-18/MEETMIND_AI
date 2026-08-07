"""
@token_required decorator — validates Supabase session tokens.

Instead of verifying JWTs locally (which requires knowing whether the
project uses HS256 or RS256), we call Supabase's own auth.get_user(token)
API. This works for all Supabase projects regardless of JWT algorithm,
requires no SUPABASE_JWT_SECRET in .env, and automatically rejects expired
or tampered tokens.

If the user row is missing from the public `users` table (e.g. the /sync
call failed when they first signed in), it is auto-created here so they
never see the "User profile not found" error on a valid session.
"""

import datetime
from functools import wraps
from flask import request, jsonify, g

from config.supabase_client import get_supabase


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header.split(" ", 1)[1].strip()

        try:
            supabase = get_supabase()
            # Let Supabase verify the token — works for HS256 and RS256 alike
            response = supabase.auth.get_user(token)
            supabase_user = response.user
        except Exception as exc:
            msg = str(exc)
            if "expired" in msg.lower():
                return jsonify({"error": "Session has expired, please sign in again"}), 401
            return jsonify({"error": f"Invalid authentication token: {msg}"}), 401

        if not supabase_user:
            return jsonify({"error": "Invalid or expired session token"}), 401

        supabase_user_id = supabase_user.id

        try:
            result = supabase.table("users").select("*").eq("id", supabase_user_id).execute()
        except Exception as exc:
            return jsonify({"error": f"Failed to load user profile: {exc}"}), 500

        if not result.data:
            # Row is missing — auto-create it from Supabase auth data so
            # users whose /sync call failed on login can still use the app.
            meta  = supabase_user.user_metadata or {}
            email = (supabase_user.email or "").lower().strip()
            now   = datetime.datetime.utcnow().isoformat() + "Z"
            record = {
                "id":              supabase_user_id,
                "email":           email,
                "name":            meta.get("full_name") or email.split("@")[0],
                "profile_picture": meta.get("avatar_url") or "",
                "timezone":        "UTC",
                "auth_provider":   "google",
                "created_at":      now,
                "updated_at":      now,
            }
            try:
                insert_result = supabase.table("users").insert(record).execute()
                user_row = insert_result.data[0]
            except Exception as exc:
                # Duplicate email (code 23505): a row already exists for this
                # email with a different UUID (e.g. a pre-migration account).
                # Fetch that row by email and re-link it to the current auth ID.
                err_str = str(exc)
                if "23505" in err_str or "duplicate key" in err_str.lower():
                    try:
                        email_result = (
                            supabase.table("users")
                            .select("*")
                            .eq("email", email)
                            .execute()
                        )
                        if email_result.data:
                            old_row = email_result.data[0]
                            old_id = old_row["id"]
                            
                            # 1. Rename old email to free up the unique constraint
                            supabase.table("users").update({"email": f"merged_{old_id}_{email}"}).eq("id", old_id).execute()
                            
                            # 2. Merge old data (passwords, tokens) into the new record
                            if old_row.get("password_hash"):
                                record["password_hash"] = old_row["password_hash"]
                            if old_row.get("auth_provider"):
                                record["auth_provider"] = old_row["auth_provider"]
                            if old_row.get("google_access_token"):
                                record["google_access_token"] = old_row["google_access_token"]
                            if old_row.get("google_refresh_token"):
                                record["google_refresh_token"] = old_row["google_refresh_token"]
                                
                            # 3. Insert the new user row
                            insert_result = supabase.table("users").insert(record).execute()
                            user_row = insert_result.data[0]
                            
                            # 4. Migrate meetings to the new ID
                            supabase.table("meetings").update({"created_by": supabase_user_id}).eq("created_by", old_id).execute()
                            
                            # 5. Delete the old user row
                            supabase.table("users").delete().eq("id", old_id).execute()
                        else:
                            return jsonify({"error": "Failed to resolve duplicate user profile"}), 500
                    except Exception as inner_exc:
                        return jsonify({"error": f"Failed to recover user profile: {inner_exc}"}), 500
                else:
                    return jsonify({"error": f"Failed to create user profile: {exc}"}), 500
        else:
            user_row = result.data[0]

        g.current_user = user_row
        g.supabase_user_id = supabase_user_id
        return f(*args, **kwargs)

    return decorated