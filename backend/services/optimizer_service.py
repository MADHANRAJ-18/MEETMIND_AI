"""
AI Meeting Optimizer.

Fetches the user's real Google Calendar events for the next 14 days,
analyses the daily meeting load, then asks the LLM to suggest specific
moves that would produce a more balanced, productive week.

Returns structured data the frontend can render as an actionable report
and apply with a single click.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from services import google_calendar_service as calendar_service
from services import ai_service


# ---------------------------------------------------------------------------
# Calendar data fetching and analysis
# ---------------------------------------------------------------------------

def _fetch_events(access_token: str, timezone_name: str, days: int = 14) -> list:
    """Fetch all calendar events for the next `days` days."""
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    return calendar_service.list_events(
        access_token,
        start.isoformat(),
        end.isoformat(),
        max_results=100,
    )


def _parse_event_minutes(event: dict, timezone_name: str) -> tuple[str, int, str, str]:
    """
    Returns (date_iso, duration_minutes, title, event_id) for a timed event.
    All-day events are skipped (return None).
    """
    start = event.get("start", {})
    end = event.get("end", {})
    if "date" in start and "dateTime" not in start:
        return None   # all-day event

    tz = ZoneInfo(timezone_name)
    try:
        start_dt = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
        end_dt = datetime.fromisoformat(end["dateTime"]).astimezone(tz)
    except (KeyError, ValueError):
        return None

    duration = max(int((end_dt - start_dt).total_seconds() / 60), 0)
    date_iso = start_dt.strftime("%Y-%m-%d")
    title = event.get("summary") or "Untitled"
    return date_iso, duration, title, event.get("id", "")


def _build_day_summaries(events: list, timezone_name: str) -> dict:
    """
    Returns { date_iso: { meetings: [...], total_minutes: int, count: int } }
    """
    days: dict = {}
    for event in events:
        parsed = _parse_event_minutes(event, timezone_name)
        if not parsed:
            continue
        date_iso, duration, title, event_id = parsed
        if date_iso not in days:
            days[date_iso] = {"meetings": [], "total_minutes": 0, "count": 0}
        days[date_iso]["meetings"].append({
            "title": title,
            "duration_minutes": duration,
            "event_id": event_id,
        })
        days[date_iso]["total_minutes"] += duration
        days[date_iso]["count"] += 1
    return days


# ---------------------------------------------------------------------------
# LLM prompt for optimization
# ---------------------------------------------------------------------------

OPTIMIZER_SYSTEM_PROMPT = """You are MeetMind AI, a scheduling optimization expert.
You receive a 14-day calendar load summary and must suggest specific, actionable
improvements that would produce a more balanced, focused week.

Return ONLY a JSON object with exactly this shape, no commentary, no markdown fences:
{
  "analysis": {
    "overloaded_days": ["YYYY-MM-DD"],
    "light_days": ["YYYY-MM-DD"],
    "avg_meetings_per_day": number,
    "busiest_day": "YYYY-MM-DD",
    "lightest_day": "YYYY-MM-DD",
    "total_meeting_hours": number
  },
  "suggestions": [
    {
      "id": "s1",
      "type": "move" | "consolidate" | "drop" | "reorder",
      "title": "Short action title (max 10 words)",
      "explanation": "1-2 sentence explanation of why this helps productivity",
      "from_date": "YYYY-MM-DD",
      "to_date": "YYYY-MM-DD",
      "meeting_title": "exact title of the meeting to move (or null)",
      "event_id": "Google Calendar event id (or null)",
      "productivity_gain": number between 5 and 40
    }
  ],
  "overall_productivity_gain": number between 5 and 40,
  "summary": "2-3 sentence plain-English summary of the optimization opportunity"
}

Rules:
- Suggest 2–5 specific moves maximum. Quality over quantity.
- Only suggest moves between days that are in the data — never invent dates.
- 'overloaded' means more than 4 hours OR more than 5 meetings in a day.
- 'light' means fewer than 2 hours of meetings.
- productivity_gain is a rough % estimate of focus time improvement from that move.
- overall_productivity_gain is the combined estimated gain if all suggestions are applied.
- Never suggest moving a meeting to a day that is also already overloaded.
- Be specific: name the actual meeting title to move, not a vague category.
- If the schedule is already well-balanced, say so in summary and return 0 suggestions."""


def analyze_and_suggest(access_token: str, timezone_name: str) -> dict:
    """
    Main entry point. Fetches calendar data, builds day summaries, asks the
    LLM for optimization suggestions, and returns the full structured result.
    """
    events = _fetch_events(access_token, timezone_name)
    day_summaries = _build_day_summaries(events, timezone_name)

    if not day_summaries:
        return {
            "analysis": {
                "overloaded_days": [], "light_days": [],
                "avg_meetings_per_day": 0, "busiest_day": None,
                "lightest_day": None, "total_meeting_hours": 0,
            },
            "suggestions": [],
            "overall_productivity_gain": 0,
            "summary": "No meetings found in your calendar for the next 14 days.",
            "day_summaries": {},
        }

    # Build the prompt payload — include enough detail for the LLM to suggest
    # specific moves using real event titles and dates.
    prompt_days = {}
    for date, data in sorted(day_summaries.items()):
        prompt_days[date] = {
            "meeting_count": data["count"],
            "total_hours": round(data["total_minutes"] / 60, 1),
            "meetings": [
                {"title": m["title"], "duration_minutes": m["duration_minutes"],
                 "event_id": m["event_id"]}
                for m in data["meetings"]
            ],
        }

    import json
    user_prompt = json.dumps({
        "timezone": timezone_name,
        "calendar": prompt_days,
    })

    try:
        result = ai_service.call_llm(OPTIMIZER_SYSTEM_PROMPT, user_prompt)
    except ai_service.AIServiceError as exc:
        raise exc

    result["day_summaries"] = {
        date: {
            "count": data["count"],
            "total_minutes": data["total_minutes"],
            "meetings": data["meetings"],
        }
        for date, data in day_summaries.items()
    }
    return result
