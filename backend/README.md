# MeetMind AI — Backend

A complete Flask backend for MeetMind AI: Google Sign-In, Google Calendar integration with
real Google Meet link generation, an AI scheduling assistant (Groq / Ollama / OpenRouter),
and Supabase persistence. JWT-protected throughout.

```
backend/
├── app.py                       # Flask app factory + entrypoint
├── requirements.txt
├── .env.example
├── config/
│   ├── settings.py              # env-based configuration
│   ├── supabase_client.py       # Supabase client singleton
│   └── google_config.py         # Google OAuth scopes/endpoints
├── routes/
│   ├── auth_routes.py           # /api/auth/*
│   ├── calendar_routes.py       # /api/calendar/*
│   ├── ai_routes.py             # /api/ai/*
│   └── meeting_routes.py        # /api/meetings*
├── services/
│   ├── google_auth_service.py   # OAuth code exchange, refresh, user upsert
│   ├── google_calendar_service.py # events / freebusy / Meet link creation
│   ├── ai_service.py            # Groq / Ollama / OpenRouter dispatch
│   ├── scheduler_service.py     # conflict detection + alternative slots
│   └── meeting_service.py       # meeting CRUD orchestration
├── middleware/
│   └── auth_middleware.py       # @token_required JWT guard
├── utils/
│   ├── jwt_handler.py
│   └── time_utils.py            # RFC3339 + free-slot math
└── database/
    └── supabase_schema.sql
```

---

## 1. Google Cloud setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create (or select) a project.
2. **Enable APIs**: APIs & Services → Library → enable:
   - **Google Calendar API**
   - **Google People API** (for `userinfo` profile/email scopes)
3. **OAuth consent screen**: APIs & Services → OAuth consent screen.
   - User type: External (or Internal if using a Google Workspace org).
   - Add scopes: `openid`, `email`, `profile`, `https://www.googleapis.com/auth/calendar`,
     `https://www.googleapis.com/auth/calendar.events`.
   - While in "Testing" mode, add your own Google account under **Test users**.
4. **Create credentials**: APIs & Services → Credentials → Create Credentials → OAuth client ID.
   - Application type: **Web application**.
   - Authorized redirect URIs: `http://localhost:5000/api/auth/google/callback`
     (must exactly match `GOOGLE_REDIRECT_URI`; add your production URL too when you deploy).
   - Copy the generated **Client ID** and **Client Secret** into `.env`.

Google Meet links are generated automatically as part of event creation (no separate API/billing
needed) by requesting `conferenceData` with `conferenceSolutionKey.type = "hangoutsMeet"` on the
Calendar `events.insert` call.

---

## 2. Supabase setup

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** → New query → paste the contents of `database/supabase_schema.sql` → Run.
   This creates the `users` and `meetings` tables, indexes, `updated_at` triggers, and RLS policies.
3. Go to **Project Settings → API** and copy:
   - **Project URL** → `SUPABASE_URL`
   - **service_role key** (not the `anon` key — the backend needs to bypass RLS) → `SUPABASE_KEY`

> ⚠️ The `service_role` key is highly privileged. Keep it server-side only — never ship it to a
> browser or mobile app.

---

## 3. LLM provider setup (pick one)

| Provider | Setup |
|---|---|
| **Groq** (recommended, fast + free tier) | Create a key at [console.groq.com](https://console.groq.com/keys) → set `GROQ_API_KEY`, `LLM_PROVIDER=groq` |
| **OpenRouter** | Create a key at [openrouter.ai/keys](https://openrouter.ai/keys) → set `OPENROUTER_API_KEY`, `LLM_PROVIDER=openrouter`. Use a `:free` model id (already defaulted). |
| **Ollama** (fully local, no API key) | Install from [ollama.com](https://ollama.com), run `ollama pull llama3.1`, then `ollama serve`. Set `LLM_PROVIDER=ollama`. |

---

## 4. Installation

Requires **Python 3.10+** (uses `zoneinfo`).

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# now edit .env with your real Supabase / Google / LLM values
```

## 5. Running the backend

```bash
# Development
python app.py
# -> running on http://localhost:5000

# Production
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

Health check: `curl http://localhost:5000/health` → `{"status": "ok"}`

---

## 6. Connecting the frontend

The provided `frontend/` folder (an updated version of the MeetMind UI) is already wired to
this backend — Google Sign-In redirects here, the JWT is captured automatically, and
creating/editing/deleting a meeting calls these APIs for real. You only need to:

1. Set `FRONTEND_URL` in `.env` to wherever you're serving `index.html` from (e.g. the URL
   shown by VS Code's Live Server, typically `http://127.0.0.1:5500`).
2. Open `frontend/script.js` and confirm `API_BASE` at the top points to this backend
   (defaults to `http://localhost:5000`).
3. Serve the frontend folder (Live Server, `python -m http.server`, etc.) and open it in
   your browser — **not** as a `file://` path, since both CORS and the OAuth redirect
   require a real `http://` origin.

How it works under the hood, in case you're customizing further:
- Clicking **Sign in with Google** redirects the browser to `GET /api/auth/google/login`.
- After Google consent, the backend redirects back to `${FRONTEND_URL}/?token=<jwt>`.
- On load, `script.js` reads `?token=` from the URL, stores it in `localStorage`, strips it
  from the address bar, then calls `GET /api/auth/me` to confirm the session and pull your
  name/email/picture into the sidebar.
- Every subsequent API call attaches `Authorization: Bearer <token>` automatically via the
  `apiFetch()` helper in `script.js`.
- Scheduling a meeting posts to `/api/meetings`; if the slot conflicts, the alternatives
  returned by the backend are shown so you can pick a different time or override it.

---

## 7. API reference & sample Postman requests

All endpoints below (except `/api/auth/google/login` and `/callback`) require:
```
Authorization: Bearer <jwt-token>
```

### Auth

**GET** `/api/auth/google/login` — open in a browser (redirects to Google).

**GET** `/api/auth/google/callback` — Google redirects here automatically.

**GET** `/api/auth/me`
```
GET http://localhost:5000/api/auth/me
Authorization: Bearer {{token}}
```
```json
{ "user": { "id": "...", "name": "Alex Kim", "email": "alex@example.com",
            "profile_picture": "https://...", "timezone": "UTC" } }
```

**POST** `/api/auth/logout`
```
POST http://localhost:5000/api/auth/logout
Authorization: Bearer {{token}}
```

### Calendar

**GET** `/api/calendar/events?time_min=2026-06-23T00:00:00Z&time_max=2026-06-30T00:00:00Z`

**POST** `/api/calendar/freebusy`
```json
{
  "time_min": "2026-06-23T00:00:00Z",
  "time_max": "2026-06-24T00:00:00Z"
}
```

**POST** `/api/calendar/create`
```json
{
  "title": "Product strategy sync",
  "description": "Quarterly planning",
  "start_datetime": "2026-06-23T10:00:00",
  "end_datetime": "2026-06-23T10:30:00",
  "timezone": "Asia/Kolkata",
  "participants": ["arun.kumar@acmestudio.com"]
}
```

**PUT** `/api/calendar/update/<event_id>` — body: any subset of the `create` fields.

**DELETE** `/api/calendar/delete/<event_id>`

### AI Assistant

**POST** `/api/ai/parse-request`
```json
{ "text": "Schedule a 30 minute sync with Karthik tomorrow at 3pm IST" }
```
```json
{ "parsed": { "title": "Sync with Karthik", "date": "2026-06-26", "time": "15:00",
              "duration_minutes": 30, "timezone": "Asia/Kolkata",
              "participants": [], "priority": "Medium" } }
```

**POST** `/api/ai/check-slots`
```json
{ "date": "2026-06-26", "time": "15:00", "duration_minutes": 30, "timezone": "Asia/Kolkata" }
```

**POST** `/api/ai/resolve-conflict` — same body as `check-slots`; returns AI-ranked alternatives.

**POST** `/api/ai/schedule-meeting` — natural language straight to a created meeting:
```json
{ "text": "Book a 45 min design review with priya@acmestudio.com next Monday at 11am IST" }
```
Returns `201` with `{ "parsed": {...}, "meeting": {...} }`, or `409` with suggested
`alternatives` if the slot conflicts (re-send with `"force": true` to book anyway).

### Meetings

**POST** `/api/meetings`
```json
{
  "title": "Engineering stand-up",
  "description": "Daily sync",
  "date": "2026-06-26",
  "start_time": "09:00",
  "duration_minutes": 30,
  "timezone": "Asia/Kolkata",
  "participants": ["karthik.rajan@acmestudio.com", "suresh.anand@acmestudio.com"]
}
```
Checks Google Calendar free/busy → creates the Calendar event + Meet link if free →
saves to Supabase → returns the saved row. On conflict, responds `409` with `alternatives`
(retry with `"force": true` to book anyway).

**GET** `/api/meetings` — list the signed-in user's meetings.

**GET** `/api/meetings/<id>`

**PUT** `/api/meetings/<id>` — partial update; any of `title`, `description`, `date`,
`start_time`, `duration_minutes`, `timezone`, `participants`, `status`.

**DELETE** `/api/meetings/<id>` — deletes the Supabase row and the Google Calendar event.

---

## 8. Security notes

- Google access/refresh tokens are stored in Supabase but **never** returned in any API
  response — `auth/me` only exposes name/email/picture/timezone.
- All `/api/calendar/*`, `/api/ai/*`, and `/api/meetings*` routes require a valid JWT via
  `@token_required`.
- `google_auth_service.get_valid_access_token()` transparently refreshes an expired Google
  access token (and persists the new one) before every Calendar call — no manual refresh step
  needed elsewhere in the codebase.
- CORS is locked to `FRONTEND_URL` only.
- Use a long, random `JWT_SECRET` in production:
  `python -c "import secrets; print(secrets.token_hex(32))"`.
