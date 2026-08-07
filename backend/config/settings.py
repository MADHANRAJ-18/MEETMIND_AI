"""
Central application configuration.

All values are loaded from environment variables (see .env.example).
Import the singleton `settings` object anywhere it is needed:

    from config.settings import settings
    settings.SUPABASE_URL
"""

import os
from dotenv import load_dotenv

load_dotenv()

for proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    if "127.0.0.1:9" in os.environ.get(proxy_name, ""):
        os.environ.pop(proxy_name, None)
class Settings:
    # --- Supabase ---
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role key
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")  # anon/public key (for frontend)
    # From Supabase Dashboard → Settings → API → JWT Settings → JWT Secret
    SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

    # --- Google Calendar API (still needed for calendar features) ---
    # These are no longer needed for auth — Supabase handles Google OAuth
    # Keep GOOGLE_CLIENT_ID/SECRET only if you use Calendar API directly

    # --- Frontend ---
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

    # --- LLM Provider ---
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()  # groq | ollama | openrouter

    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.environ.get(
        "OPENROUTER_MODEL", "meta-llama/llama-3.1-70b-instruct:free"
    )

    # --- Email Invitations ---
    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

    # --- Server ---
    PORT = int(os.environ.get("PORT", "5000"))
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")

    def validate(self):
        required = [
            "SUPABASE_URL",
            "SUPABASE_KEY",
            "SUPABASE_ANON_KEY",
            "SUPABASE_JWT_SECRET",
        ]
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            print(
                f"[WARNING] Missing required environment variables: {', '.join(missing)}. "
                "The backend will start but related features will fail until these are set."
            )


settings = Settings()
settings.validate()



