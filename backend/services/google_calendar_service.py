"""
Thin wrapper around the Google Calendar v3 API.

Every function takes a valid OAuth access token (already refreshed by
google_auth_service if necessary) and builds a per-request `Credentials`
object - there is no server-side session, so this stays fully stateless.
"""

import uuid
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def _get_service(access_token: str):
    creds = Credentials(token=access_token)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def list_events(access_token: str, time_min: str, time_max: str, calendar_id: str = "primary", max_results: int = 50) -> list:
    service = _get_service(access_token)
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        )
        .execute()
    )
    return result.get("items", [])


def get_freebusy(access_token: str, time_min: str, time_max: str, calendar_id: str = "primary") -> list:
    """Returns a list of {"start": rfc3339, "end": rfc3339} busy intervals."""
    service = _get_service(access_token)
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    return result.get("calendars", {}).get(calendar_id, {}).get("busy", [])



def get_event(access_token: str, event_id: str, calendar_id: str = "primary") -> dict:
    service = _get_service(access_token)
    return service.events().get(calendarId=calendar_id, eventId=event_id).execute()

def create_event(access_token: str, event_data: dict, calendar_id: str = "primary", send_updates: str = "all"):
    """
    event_data keys: title, description, start_datetime, end_datetime
    (RFC3339), timezone (IANA), participants (list of emails).
    Returns (created_event_dict, meet_link_or_none).
    """
    service = _get_service(access_token)

    body = {
        "summary": event_data["title"],
        "description": event_data.get("description", ""),
        "start": {
            "dateTime": event_data["start_datetime"],
            "timeZone": event_data["timezone"],
        },
        "end": {
            "dateTime": event_data["end_datetime"],
            "timeZone": event_data["timezone"],
        },
        "attendees": [{"email": email} for email in event_data.get("participants", [])],
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "reminders": {"useDefault": True},
    }

    created = (
        service.events()
        .insert(
            calendarId=calendar_id,
            body=body,
            conferenceDataVersion=1,
            sendUpdates=send_updates,
        )
        .execute()
    )

    meet_link = None
    for entry_point in created.get("conferenceData", {}).get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            meet_link = entry_point.get("uri")
            break

    return created, meet_link


def update_event(access_token: str, event_id: str, event_data: dict, calendar_id: str = "primary", send_updates: str = "all"):
    """Partial update (PATCH) - only fields present in event_data are changed."""
    service = _get_service(access_token)

    body = {}
    if "title" in event_data:
        body["summary"] = event_data["title"]
    if "description" in event_data:
        body["description"] = event_data["description"]
    if "start_datetime" in event_data and "timezone" in event_data:
        body["start"] = {"dateTime": event_data["start_datetime"], "timeZone": event_data["timezone"]}
    if "end_datetime" in event_data and "timezone" in event_data:
        body["end"] = {"dateTime": event_data["end_datetime"], "timeZone": event_data["timezone"]}
    if "participants" in event_data:
        body["attendees"] = [{"email": email} for email in event_data["participants"]]

    updated = (
        service.events()
        .patch(calendarId=calendar_id, eventId=event_id, body=body, sendUpdates=send_updates)
        .execute()
    )
    return updated


def delete_event(access_token: str, event_id: str, calendar_id: str = "primary", send_updates: str = "all"):
    service = _get_service(access_token)
    service.events().delete(
        calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates
    ).execute()

