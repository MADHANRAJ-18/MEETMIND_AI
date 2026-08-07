"""
Meeting CRUD orchestration: combines Google Calendar (event + Meet link
creation/update/delete), the scheduler (conflict detection), and Supabase
(persistence) behind a single clean interface used by routes/meeting_routes.py
and routes/ai_routes.py.
"""

from datetime import datetime, timezone as dt_timezone

from config.supabase_client import get_supabase
from services import google_calendar_service as calendar_service
from services import google_auth_service
from services import scheduler_service
from services import email_service
from utils import time_utils



def _participant_acceptance_status(event: dict, participants: list) -> str:
    participant_emails = {str(email).lower() for email in (participants or []) if email}
    if not participant_emails:
        return "scheduled"

    attendee_statuses = {}
    for attendee in event.get("attendees", []) or []:
        email = (attendee.get("email") or "").lower()
        if email in participant_emails:
            attendee_statuses[email] = attendee.get("responseStatus") or "needsAction"

    if any(attendee_statuses.get(email) == "declined" for email in participant_emails):
        return "pending"
    if participant_emails and all(attendee_statuses.get(email) == "accepted" for email in participant_emails):
        return "scheduled"
    return "pending"


def _apply_acceptance_status(meeting: dict, access_token: str) -> dict:
    if not meeting or not meeting.get("google_event_id"):
        return meeting
    participants = meeting.get("participants") or []
    if not participants:
        meeting["status"] = "scheduled"
        return meeting
    try:
        event = calendar_service.get_event(access_token, meeting["google_event_id"])
        meeting["status"] = _participant_acceptance_status(event, participants)
    except Exception:
        meeting["status"] = "pending"
    return meeting
class MeetingServiceError(Exception):
    def __init__(self, message, status_code=400, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def create_meeting(user: dict, data: dict) -> dict:
    required = ["title", "date", "start_time", "duration_minutes"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        raise MeetingServiceError(f"Missing required fields: {', '.join(missing)}")

    timezone_name = data.get("timezone") or user.get("timezone") or "UTC"
    duration = int(data["duration_minutes"])
    participants = data.get("participants", [])

    access_token = google_auth_service.get_valid_access_token(user)

    check = scheduler_service.check_conflict_and_suggest(
        access_token, data["date"], data["start_time"], duration, timezone_name
    )

    if check["conflict"] and not data.get("force"):
        raise MeetingServiceError(
            "The requested time conflicts with an existing event on your calendar.",
            status_code=409,
            payload={"conflict": True, "alternatives": check["alternatives"]},
        )

    start_dt_str = check["requested"]["start_datetime"]
    end_dt_str = check["requested"]["end_datetime"]

    # Use the AI-generated email body as the event description so it appears
    # in the Google Calendar invite email that attendees receive.
    # Fall back to the plain meeting description if no AI body was provided.
    ai_body = (data.get("email_body") or "").strip()
    plain_description = (data.get("description") or "").strip()
    event_description = ai_body or plain_description

    event_data = {
        "title": data["title"],
        "description": event_description,
        "start_datetime": start_dt_str,
        "end_datetime": end_dt_str,
        "timezone": timezone_name,
        "participants": participants,
    }
    created_event, meet_link = calendar_service.create_event(access_token, event_data)

    supabase = get_supabase()
    now_iso = datetime.now(dt_timezone.utc).isoformat()
    record = {
        "title": data["title"],
        "description": plain_description,   # store the plain description in DB, not the full email body
        "meeting_date": data["date"],
        "start_time": data["start_time"],
        "end_time": time_utils.parse_rfc3339(end_dt_str).strftime("%H:%M"),
        "timezone": timezone_name,
        "participants": participants,
        "meeting_link": meet_link,
        "google_event_id": created_event.get("id"),
        "conflict_status": "resolved" if check["conflict"] else "none",
        "status": "scheduled",
        "created_by": user["id"],
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    result = supabase.table("meetings").insert(record).execute()
    meeting = result.data[0]
    # Google Calendar already emails all attendees via sendUpdates="all" when
    # the event is created — no need to send a second email here.
    meeting["email_status"] = {"sent": True, "method": "google_calendar"}
    return _apply_acceptance_status(meeting, access_token)


def list_meetings(user: dict) -> list:
    supabase = get_supabase()
    result = (
        supabase.table("meetings")
        .select("*")
        .eq("created_by", user["id"])
        .order("meeting_date", desc=False)
        .order("start_time", desc=False)
        .execute()
    )
    try:
        access_token = google_auth_service.get_valid_access_token(user)
    except google_auth_service.GoogleAuthError:
        return result.data
    return [_apply_acceptance_status(meeting, access_token) for meeting in result.data]


def get_meeting(user: dict, meeting_id: str) -> dict:
    supabase = get_supabase()
    result = (
        supabase.table("meetings")
        .select("*")
        .eq("id", meeting_id)
        .eq("created_by", user["id"])
        .execute()
    )
    if not result.data:
        raise MeetingServiceError("Meeting not found", status_code=404)
    return result.data[0]


def update_meeting(user: dict, meeting_id: str, data: dict) -> dict:
    meeting = get_meeting(user, meeting_id)
    access_token = google_auth_service.get_valid_access_token(user)

    timezone_name = data.get("timezone", meeting["timezone"])
    date_str = data.get("date", meeting["meeting_date"])
    time_str = data.get("start_time", meeting["start_time"])
    duration = int(data.get("duration_minutes", 30))

    start_dt_str = time_utils.to_rfc3339(date_str, time_str, timezone_name)
    end_dt_str = time_utils.add_minutes(start_dt_str, duration)

    event_data = {
        "title": data.get("title", meeting["title"]),
        "description": data.get("description", meeting["description"]),
        "start_datetime": start_dt_str,
        "end_datetime": end_dt_str,
        "timezone": timezone_name,
        "participants": data.get("participants", meeting["participants"]),
    }

    if meeting.get("google_event_id"):
        calendar_service.update_event(access_token, meeting["google_event_id"], event_data)

    supabase = get_supabase()
    update_record = {
        "title": event_data["title"],
        "description": event_data["description"],
        "meeting_date": date_str,
        "start_time": time_str,
        "end_time": time_utils.parse_rfc3339(end_dt_str).strftime("%H:%M"),
        "timezone": timezone_name,
        "participants": event_data["participants"],
        "status": data.get("status", meeting["status"]),
        "updated_at": datetime.now(dt_timezone.utc).isoformat(),
    }
    result = supabase.table("meetings").update(update_record).eq("id", meeting_id).execute()
    return result.data[0]


def delete_meeting(user: dict, meeting_id: str) -> None:
    meeting = get_meeting(user, meeting_id)
    access_token = google_auth_service.get_valid_access_token(user)

    if meeting.get("google_event_id"):
        try:
            calendar_service.delete_event(access_token, meeting["google_event_id"])
        except Exception:
            pass

    supabase = get_supabase()
    supabase.table("meetings").delete().eq("id", meeting_id).execute()


def get_participant_statuses(user: dict, meeting_id: str) -> list:
    """
    Returns a list of { email, status, displayName } for every participant
    on the Google Calendar event, reflecting their current response:
    needsAction | accepted | declined | tentative
    """
    meeting = get_meeting(user, meeting_id)
    google_event_id = meeting.get("google_event_id")
    if not google_event_id:
        return []
    try:
        access_token = google_auth_service.get_valid_access_token(user)
        event = calendar_service.get_event(access_token, google_event_id)
        attendees = event.get("attendees") or []
        participants = {str(e).lower() for e in (meeting.get("participants") or []) if e}
        return [
            {
                "email": a.get("email", "").lower(),
                "status": a.get("responseStatus") or "needsAction",
                "displayName": a.get("displayName") or a.get("email", ""),
            }
            for a in attendees
            if a.get("email", "").lower() in participants
        ]
    except Exception:
        return []






