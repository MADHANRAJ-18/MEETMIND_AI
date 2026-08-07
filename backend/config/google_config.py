"""
Google API endpoint URLs and Calendar scopes.
OAuth flow is now handled by Supabase Auth — these are only used
for the Calendar API and token refresh calls.
"""

GOOGLE_TOKEN_URI    = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URI   = "https://oauth2.googleapis.com/revoke"

# Scopes requested via Supabase Auth's Google provider configuration.
# Make sure these match what you enabled in your Supabase Google provider settings.
GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]
