"""
Direct Google Calendar access routes (lower-level than /api/meetings):
  GET    /api/calendar/events
  POST   /api/calendar/freebusy
  POST   /api/calendar/create
  PUT    /api/calendar/update/<event_id>
  DELETE /api/calendar/delete/<event_id>
"""

from flask import Blueprint, request, jsonify, g
from googleapiclient.errors import HttpError

from middleware.auth_middleware import token_required
from services import google_calendar_service as calendar_service
from services import google_auth_service

calendar_bp = Blueprint("calendar_bp", __name__, url_prefix="/api/calendar")


@calendar_bp.route("/events", methods=["GET"])
@token_required
def list_events():
    user = g.current_user
    time_min = request.args.get("time_min")
    time_max = request.args.get("time_max")
    if not time_min or not time_max:
        return jsonify({"error": "time_min and time_max query params are required (RFC3339)"}), 400

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        events = calendar_service.list_events(access_token, time_min, time_max)
        return jsonify({"events": events})
    except HttpError as exc:
        return jsonify({"error": f"Google Calendar error: {exc}"}), 502
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@calendar_bp.route("/freebusy", methods=["POST"])
@token_required
def freebusy():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    time_min = body.get("time_min")
    time_max = body.get("time_max")
    if not time_min or not time_max:
        return jsonify({"error": "time_min and time_max are required (RFC3339)"}), 400

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        busy = calendar_service.get_freebusy(access_token, time_min, time_max)
        return jsonify({"busy": busy})
    except HttpError as exc:
        return jsonify({"error": f"Google Calendar error: {exc}"}), 502
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@calendar_bp.route("/create", methods=["POST"])
@token_required
def create_event():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    required = ["title", "start_datetime", "end_datetime", "timezone"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        event, meet_link = calendar_service.create_event(access_token, body)
        return jsonify({"event": event, "meeting_link": meet_link}), 201
    except HttpError as exc:
        return jsonify({"error": f"Google Calendar error: {exc}"}), 502
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@calendar_bp.route("/update/<event_id>", methods=["PUT"])
@token_required
def update_event(event_id):
    user = g.current_user
    body = request.get_json(silent=True) or {}

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        event = calendar_service.update_event(access_token, event_id, body)
        return jsonify({"event": event})
    except HttpError as exc:
        return jsonify({"error": f"Google Calendar error: {exc}"}), 502
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@calendar_bp.route("/delete/<event_id>", methods=["DELETE"])
@token_required
def delete_event(event_id):
    user = g.current_user

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        calendar_service.delete_event(access_token, event_id)
        return jsonify({"message": "Event deleted successfully"})
    except HttpError as exc:
        return jsonify({"error": f"Google Calendar error: {exc}"}), 502
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
