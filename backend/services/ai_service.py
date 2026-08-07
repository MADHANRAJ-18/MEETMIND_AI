"""
AI assistant service. Talks to whichever free LLM provider is configured
(Groq, Ollama, or OpenRouter) via LLM_PROVIDER, and exposes meeting-specific
helpers that always return structured JSON:

  - parse_meeting_request(text, ...)   -> extract title/date/time/etc.
  - resolve_conflict(...)              -> pick/explain the best alternative slot
"""

import json
import requests

from config.settings import settings


class AIServiceError(Exception):
    pass


# ---------------------------------------------------------------------------
# Low-level provider calls (all OpenAI-style chat-completions except Ollama)
# ---------------------------------------------------------------------------

def _call_groq(system_prompt: str, user_prompt: str) -> str:
    if not settings.GROQ_API_KEY:
        raise AIServiceError("GROQ_API_KEY is not set")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        raise AIServiceError(f"Groq API error ({resp.status_code}): {resp.text}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_openrouter(system_prompt: str, user_prompt: str) -> str:
    if not settings.OPENROUTER_API_KEY:
        raise AIServiceError("OPENROUTER_API_KEY is not set")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        raise AIServiceError(f"OpenRouter API error ({resp.status_code}): {resp.text}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_ollama(system_prompt: str, user_prompt: str) -> str:
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
    }
    try:
        resp = requests.post(url, json=payload, timeout=60)
    except requests.RequestException as exc:
        raise AIServiceError(
            f"Could not reach Ollama at {settings.OLLAMA_BASE_URL}. Is it running? ({exc})"
        ) from exc
    if not resp.ok:
        raise AIServiceError(f"Ollama error ({resp.status_code}): {resp.text}")
    data = resp.json()
    return data["message"]["content"]


def call_llm(system_prompt: str, user_prompt: str) -> dict:
    """Dispatch to the configured provider and parse the JSON response."""
    provider = settings.LLM_PROVIDER

    try:
        if provider == "groq":
            raw = _call_groq(system_prompt, user_prompt)
        elif provider == "openrouter":
            raw = _call_openrouter(system_prompt, user_prompt)
        elif provider == "ollama":
            raw = _call_ollama(system_prompt, user_prompt)
        else:
            raise AIServiceError(
                f"Unsupported LLM_PROVIDER '{provider}'. Use groq, ollama, or openrouter."
            )
    except requests.RequestException as exc:
        raise AIServiceError(f"LLM request failed: {exc}") from exc

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIServiceError(f"LLM did not return valid JSON. Raw output: {raw[:500]}") from exc


# ---------------------------------------------------------------------------
# Meeting-specific prompts
# ---------------------------------------------------------------------------

PARSE_SYSTEM_PROMPT = """You are MeetMind AI, a scheduling assistant embedded in a calendar app.
Extract structured meeting details from the user's natural language request.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{
  "title": string,
  "description": string,
  "date": "YYYY-MM-DD" or null,
  "time": "HH:MM" in 24-hour format or null,
  "duration_minutes": integer,
  "timezone": IANA timezone string (e.g. "Asia/Kolkata"),
  "participants": [array of email addresses mentioned or clearly implied],
  "priority": "Low" | "Medium" | "High"
}

Rules:
- If a field cannot be determined: title -> a short reasonable guess, description -> "",
  date/time -> null, duration_minutes -> 30, timezone -> the default provided, participants -> [].
- Resolve relative dates ("tomorrow", "next Monday", "Friday") using the reference date given.
- Never invent participant emails that were not mentioned or clearly implied (e.g. a first name
  alone is not enough to invent an email)."""


def parse_meeting_request(text: str, reference_date: str, default_timezone: str) -> dict:
    user_prompt = (
        f"Reference date (today): {reference_date}\n"
        f"Default timezone if none specified: {default_timezone}\n"
        f"Meeting request: \"{text}\"\n\n"
        "Return the JSON object now."
    )
    return call_llm(PARSE_SYSTEM_PROMPT, user_prompt)


CONFLICT_SYSTEM_PROMPT = """You are MeetMind AI, resolving a calendar scheduling conflict.
You are given the originally requested meeting slot, the user's busy periods, and a list of
already-computed free candidate slots.

Return ONLY a JSON object with exactly this shape:
{
  "has_conflict": boolean,
  "conflict_reason": string,
  "recommended_slot": {"date": "YYYY-MM-DD", "time": "HH:MM", "timezone": string} or null,
  "alternative_slots": [
    {"date": "YYYY-MM-DD", "time": "HH:MM", "timezone": string, "reason": string}
  ]
}

Rules:
- Only choose alternative_slots from the candidate_slots provided - do not invent new times.
- Return at most 3 alternative_slots, ordered best-first (closest to the original time,
  same day preferred, then next business day, always within working hours).
- recommended_slot should be the single best alternative (or null if there was no conflict)."""


def resolve_conflict(requested_slot: dict, busy_periods: list, candidate_slots: list) -> dict:
    user_prompt = json.dumps(
        {
            "requested_slot": requested_slot,
            "busy_periods": busy_periods,
            "candidate_slots": candidate_slots,
        },
        default=str,
    )
    return call_llm(CONFLICT_SYSTEM_PROMPT, user_prompt)


# ---------------------------------------------------------------------------
# Conversational chat assistant
# ---------------------------------------------------------------------------

CHAT_INTENT_SYSTEM_PROMPT = """You are the intent router for MeetMind AI, a scheduling
assistant embedded in a calendar app's chat widget. Read the user's message (and the
short recent chat history, if any) and decide what they want.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{
  "intent": "show_free_slots" | "create_meeting" | "resolve_conflict" | "general",
  "date": "YYYY-MM-DD" or null,
  "time": "HH:MM" in 24-hour format or null,
  "duration_minutes": integer or null,
  "title": string or null,
  "description": string or null,
  "participants": [array of email addresses mentioned or clearly implied],
  "timezone": IANA timezone string or null
}

Intent definitions:
- "show_free_slots": the user wants to know open/available time, with no single specific
  time they want checked (e.g. "when am I free tomorrow", "show my open slots this week").
- "create_meeting": the user wants a meeting actually scheduled/created/booked, and gave
  (or clearly implied) enough info to attempt it - a topic and at least a rough date or time.
- "resolve_conflict": the user is asking about a specific date+time and wants to know if
  it's free, or explicitly mentions a clash/conflict/double-booking at a specific slot.
- "general": greetings, thanks, unclear requests, or questions unrelated to scheduling.

Rules:
- Resolve relative dates ("tomorrow", "next Monday", "this week") using the reference date given.
- "this week" / no specific day for show_free_slots -> date: the reference date (search from today).
- Never invent participant emails that were not mentioned or clearly implied.
- duration_minutes defaults to 30 if not mentioned and intent involves a time slot.
- For "general" intent, all fields besides "intent" should be null/empty."""


def classify_chat_intent(message: str, reference_date: str, default_timezone: str, history: list = None) -> dict:
    history_lines = ""
    if history:
        history_lines = "\n".join(
            f"{h.get('role', 'user')}: {h.get('content', '')}" for h in history[-6:]
        )
    user_prompt = (
        f"Reference date (today): {reference_date}\n"
        f"Default timezone if none specified: {default_timezone}\n"
        + (f"Recent conversation:\n{history_lines}\n\n" if history_lines else "")
        + f"Latest user message: \"{message}\"\n\n"
        "Return the JSON object now."
    )
    return call_llm(CHAT_INTENT_SYSTEM_PROMPT, user_prompt)


CHAT_REPLY_SYSTEM_PROMPT = """You are MeetMind AI, a friendly, concise scheduling assistant
speaking directly to the user inside a chat widget. You are given the structured result of
an action that already happened (showing free slots, resolving a conflict, creating a
meeting, or an error). Write a short, natural chat reply summarizing it for the user.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{ "reply": string }

Rules:
- Keep it conversational and brief (1-4 sentences, or a short list for multiple slots).
- Use times/dates exactly as given in the data - never invent times that aren't present.
- If listing slots, format them naturally, e.g. "Tuesday, Jun 23 at 10:30 AM".
- If a meeting was created, confirm the title, date, and time, and mention the meeting
  link only if one is present in the data.
- If there was an error, apologize briefly and clearly state what's needed to fix it."""


def generate_chat_reply(action: str, data: dict) -> str:
    user_prompt = json.dumps({"action": action, "data": data}, default=str)
    try:
        result = call_llm(CHAT_REPLY_SYSTEM_PROMPT, user_prompt)
        return result.get("reply") or ""
    except AIServiceError:
        return ""


# ---------------------------------------------------------------------------
# Email draft generator
# ---------------------------------------------------------------------------

EMAIL_DRAFT_SYSTEM_PROMPT = """You are MeetMind AI, writing a professional meeting invitation
email on behalf of the organizer. Given the meeting details, write a warm, clear, and concise
invitation email that participants will actually want to read and respond to.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{
  "subject": string,
  "body": string
}

Rules:
- Subject: clear and specific, e.g. "Invitation: Q3 Product Strategy — Thu Jul 10, 10:30 AM"
- Body: 3-5 short paragraphs. Open with a warm greeting, state the purpose, include the key
  details (date, time, duration, timezone, Meet link if provided), close with a clear call to
  action asking them to accept the calendar invite.
- Tone: professional but friendly. Not overly formal, not too casual.
- Never invent details not given in the meeting data.
- If description/agenda is provided, include a brief "Agenda" section.
- Sign off with the organizer's name.
- Use plain text only — no markdown, no bullet symbols, no HTML."""


def generate_email_draft(meeting: dict, organizer: dict) -> dict:
    """
    Returns {"subject": str, "body": str} for the meeting invitation email.
    Falls back to a plain template if the LLM call fails.
    """
    organizer_name = organizer.get("name") or organizer.get("email") or "The organizer"
    title = meeting.get("title") or "Meeting"
    date = meeting.get("meeting_date") or meeting.get("date") or ""
    start = str(meeting.get("start_time") or "")[:5]
    duration = meeting.get("duration_minutes") or 30
    timezone = meeting.get("timezone") or "UTC"
    description = meeting.get("description") or ""
    meet_link = meeting.get("meeting_link") or ""
    participants = meeting.get("participants") or []

    user_prompt = json.dumps({
        "meeting": {
            "title": title,
            "date": date,
            "start_time": start,
            "duration_minutes": duration,
            "timezone": timezone,
            "description": description,
            "meeting_link": meet_link,
            "participant_count": len(participants),
        },
        "organizer_name": organizer_name,
    }, default=str)

    try:
        result = call_llm(EMAIL_DRAFT_SYSTEM_PROMPT, user_prompt)
        subject = result.get("subject") or f"Meeting invite: {title}"
        body = result.get("body") or ""
        if subject and body:
            return {"subject": subject, "body": body}
    except AIServiceError:
        pass

    # Plain fallback if LLM fails
    lines = [
        f"{organizer_name} has invited you to a meeting.",
        "",
        f"Title: {title}",
        f"Date: {date}",
        f"Time: {start} ({timezone})",
        f"Duration: {duration} minutes",
    ]
    if description:
        lines += ["", f"Agenda: {description}"]
    if meet_link:
        lines += ["", f"Google Meet: {meet_link}"]
    lines += ["", "Please accept the calendar invitation to confirm your attendance.", "", f"Best regards,\n{organizer_name}"]
    return {
        "subject": f"Meeting invite: {title}",
        "body": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# Meeting Necessity Checker
# ---------------------------------------------------------------------------

NECESSITY_CHECK_SYSTEM_PROMPT = """You are MeetMind AI's meeting necessity checker.
Given a proposed meeting's details, decide whether it truly needs to be a synchronous
meeting, or whether it could be better handled asynchronously.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{
  "verdict": "necessary" | "email_instead" | "async_instead" | "overload_risk",
  "reason": "One clear sentence explaining the verdict.",
  "suggestion": "One concrete alternative action if verdict is not 'necessary'. Null if necessary.",
  "confidence": number between 0 and 1
}

Verdict definitions:
- "necessary": This topic genuinely benefits from real-time discussion, decision-making,
  brainstorming, sensitive conversation, or requires immediate two-way interaction.
- "email_instead": This is purely information-sharing (reports, announcements, status
  updates, FYIs) where no real-time discussion is needed. An email or document works better.
- "async_instead": This could be handled via a shared doc, Slack thread, or recorded
  Loom/video — it needs some back-and-forth but not a live meeting.
- "overload_risk": Flag this only when the meeting count context shows >= 4 meetings already
  on that day (passed in the prompt). The meeting might be valid but the day is too full.

Rules:
- Short meetings (15 min or less) for information sharing are almost always email_instead.
- Status updates, report sharing, announcements → email_instead.
- Brainstorming, decisions, conflict resolution, onboarding, sensitive feedback → necessary.
- Code reviews, document feedback, async-friendly Q&A → async_instead.
- If unsure, lean toward "necessary" — don't block meetings without good reason.
- confidence below 0.6 should return "necessary" unless it's clearly email_instead."""


def check_meeting_necessity(
    title: str,
    description: str,
    duration_minutes: int,
    existing_meetings_on_day: int = 0,
) -> dict:
    """
    Returns { verdict, reason, suggestion, confidence }.
    verdict is one of: necessary | email_instead | async_instead | overload_risk
    """
    user_prompt = json.dumps({
        "proposed_meeting": {
            "title": title,
            "description": description or "",
            "duration_minutes": duration_minutes,
        },
        "existing_meetings_on_that_day": existing_meetings_on_day,
    })
    try:
        return call_llm(NECESSITY_CHECK_SYSTEM_PROMPT, user_prompt)
    except AIServiceError:
        return {"verdict": "necessary", "reason": "", "suggestion": None, "confidence": 1.0}


# ---------------------------------------------------------------------------
# Timetable slot explanation
# ---------------------------------------------------------------------------

TIMETABLE_SLOT_SYSTEM_PROMPT = """You are MeetMind AI helping schedule a meeting for
an organization. Given a list of recommended time slots (each with a date, time,
and list of available participants), write a short, clear explanation for why each
slot was chosen.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{
  "explanations": [
    {
      "slot_index": 0,
      "rank": "Best slot",
      "explanation": "One or two sentences explaining why this slot works well."
    }
  ]
}

Rules:
- Rank them as "Best slot", "Second best", "Third best", etc.
- Mention specific reasons: all participants free, good time of day, no conflicts.
- Keep each explanation under 30 words.
- If a slot has fewer available participants than requested, mention who is missing."""


def explain_timetable_slots(slots: list, requested_participants: list) -> list:
    """
    Takes up to 5 slot dicts and returns them with an AI-generated explanation
    and rank label added to each.
    """
    if not slots:
        return slots

    user_prompt = json.dumps({
        "requested_participants": requested_participants,
        "slots": [
            {
                "index": i,
                "date": s.get("date"),
                "day": s.get("day"),
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "available": s.get("available", []),
                "conflict_score": s.get("conflict_score", 0),
            }
            for i, s in enumerate(slots)
        ],
    })

    try:
        result = call_llm(TIMETABLE_SLOT_SYSTEM_PROMPT, user_prompt)
        explanations = {e["slot_index"]: e for e in (result.get("explanations") or [])}
        for i, slot in enumerate(slots):
            ex = explanations.get(i, {})
            slot["rank"] = ex.get("rank") or f"Option {i + 1}"
            slot["explanation"] = ex.get("explanation") or "Available for all selected participants."
    except AIServiceError:
        for i, slot in enumerate(slots):
            slot["rank"] = ["Best slot", "Second best", "Third best", "Option 4", "Option 5"][i] if i < 5 else f"Option {i+1}"
            slot["explanation"] = "Available for all selected participants."

    return slots
