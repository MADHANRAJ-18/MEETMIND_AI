"""
Meeting CRUD routes (combines Supabase persistence with Google Calendar +
Meet link creation under the hood, via services/meeting_service.py):
  POST   /api/meetings
  GET    /api/meetings
  GET    /api/meetings/<id>
  PUT    /api/meetings/<id>
  DELETE /api/meetings/<id>
"""

from flask import Blueprint, request, jsonify, g

from middleware.auth_middleware import token_required
from services import meeting_service
from services import google_auth_service

meeting_bp = Blueprint("meeting_bp", __name__, url_prefix="/api/meetings")


@meeting_bp.route("", methods=["POST"])
@token_required
def create_meeting():
    user = g.current_user
    body = request.get_json(silent=True) or {}

    try:
        meeting = meeting_service.create_meeting(user, body)
        return jsonify({"meeting": meeting}), 201
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc), **exc.payload}), exc.status_code
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@meeting_bp.route("", methods=["GET"])
@token_required
def list_meetings():
    user = g.current_user
    try:
        meetings = meeting_service.list_meetings(user)
        return jsonify({"meetings": meetings})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@meeting_bp.route("/<meeting_id>", methods=["GET"])
@token_required
def get_meeting(meeting_id):
    user = g.current_user
    try:
        meeting = meeting_service.get_meeting(user, meeting_id)
        return jsonify({"meeting": meeting})
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


@meeting_bp.route("/<meeting_id>", methods=["PUT"])
@token_required
def update_meeting(meeting_id):
    user = g.current_user
    body = request.get_json(silent=True) or {}

    try:
        meeting = meeting_service.update_meeting(user, meeting_id, body)
        return jsonify({"meeting": meeting})
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@meeting_bp.route("/<meeting_id>", methods=["DELETE"])
@token_required
def delete_meeting(meeting_id):
    user = g.current_user
    try:
        meeting_service.delete_meeting(user, meeting_id)
        return jsonify({"message": "Meeting deleted successfully"})
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@meeting_bp.route("/<meeting_id>/participant-status", methods=["GET"])
@token_required
def participant_status(meeting_id):
    """
    Returns current Google Calendar attendee response statuses for a meeting.
    Used by the frontend poller to detect when participants accept/decline.
    """
    user = g.current_user
    try:
        statuses = meeting_service.get_participant_statuses(user, meeting_id)
        return jsonify({"statuses": statuses})
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@meeting_bp.route("/reschedule-event", methods=["POST"])
@token_required
def reschedule_event():
    """
    Move a Google Calendar event to a new date, keeping the same time and
    duration. Used by the Meeting Optimizer's Apply button.

    Body: { "event_id": str, "to_date": "YYYY-MM-DD", "timezone": str }
    """
    user = g.current_user
    body = request.get_json(silent=True) or {}
    event_id = body.get("event_id")
    to_date = body.get("to_date")
    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"

    if not event_id or not to_date:
        return jsonify({"error": "event_id and to_date are required"}), 400

    try:
        from services import google_auth_service, google_calendar_service as cal
        from zoneinfo import ZoneInfo
        from datetime import datetime

        access_token = google_auth_service.get_valid_access_token(user)
        event = cal.get_event(access_token, event_id)

        # Preserve original start/end times, only change the date
        orig_start = event.get("start", {})
        orig_end = event.get("end", {})

        if "dateTime" not in orig_start:
            return jsonify({"error": "Cannot reschedule an all-day event"}), 400

        tz = ZoneInfo(timezone_name)
        start_dt = datetime.fromisoformat(orig_start["dateTime"]).astimezone(tz)
        end_dt = datetime.fromisoformat(orig_end["dateTime"]).astimezone(tz)
        new_start_dt = start_dt.replace(
            year=int(to_date[:4]),
            month=int(to_date[5:7]),
            day=int(to_date[8:10])
        )
        duration = end_dt - start_dt
        new_end_dt = new_start_dt + duration

        event_data = {
            "title": event.get("summary", ""),
            "description": event.get("description", ""),
            "start_datetime": new_start_dt.isoformat(),
            "end_datetime": new_end_dt.isoformat(),
            "timezone": timezone_name,
            "participants": [a["email"] for a in event.get("attendees", []) if "email" in a],
        }
        updated = cal.update_event(access_token, event_id, event_data)
        return jsonify({"event": updated})

    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
