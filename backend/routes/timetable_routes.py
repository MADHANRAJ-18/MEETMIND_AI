"""
Smart Timetable Scheduler — Flask routes.

POST   /api/timetable/upload           Upload CSV or Excel file
POST   /api/timetable/manual           Add a single entry manually
GET    /api/timetable                  List all entries (with optional filters)
PUT    /api/timetable/update/<id>      Update an entry
DELETE /api/timetable/delete/<id>      Delete an entry
POST   /api/timetable/find-common-slot Find best common slots (AI-powered)
POST   /api/timetable/check-conflicts  Check a specific slot for conflicts
POST   /api/timetable/schedule         Schedule meeting from a chosen slot
"""

import io
from flask import Blueprint, request, jsonify, g

from middleware.auth_middleware import token_required
from services import timetable_service, ai_service
from services import google_auth_service, google_calendar_service as cal_service
from services import meeting_service, email_service

timetable_bp = Blueprint("timetable_bp", __name__, url_prefix="/api/timetable")


# ---------------------------------------------------------------------------
# Upload CSV / Excel
# ---------------------------------------------------------------------------
@timetable_bp.route("/organizations", methods=["GET"])
@token_required
def list_organizations():
    """Return all distinct organization names uploaded by this user."""
    orgs = timetable_service.list_organization_names(g.current_user["id"])
    return jsonify({"organizations": orgs})


@timetable_bp.route("/preview-upload", methods=["POST"])
@token_required
def preview_upload():
    """
    Parse and validate an uploaded file WITHOUT saving to the database.
    Returns a preview of valid rows and any errors so the user can decide
    whether to save or discard before any data is written.

    Multipart fields: file, organization_type, organization_name
    Response: { valid: [...], errors: [...], total_rows: int }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    f = request.files["file"]
    filename = (f.filename or "").lower()
    file_bytes = f.read()
    org_type = request.form.get("organization_type") or ""
    org_name = request.form.get("organization_name") or ""

    try:
        if filename.endswith(".csv"):
            rows = timetable_service.parse_csv(file_bytes)
        elif filename.endswith((".xlsx", ".xls")):
            rows = _parse_excel(file_bytes)
        else:
            return jsonify({"error": "Only .csv and .xlsx files are supported."}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not parse file: {exc}"}), 400

    result = timetable_service.parse_only(rows, org_type, org_name)
    return jsonify({
        "valid": result["valid"],
        "errors": result["errors"][:10],
        "total_errors": len(result["errors"]),
        "total_rows": len(rows),
        "valid_count": len(result["valid"]),
    })


@timetable_bp.route("/delete-participant", methods=["DELETE"])
@token_required
def delete_participant():
    """
    Delete ALL timetable entries for one participant within an organization.
    Body: { "participant_name": str, "organization_name": str }
    """
    body = request.get_json(silent=True) or {}
    participant_name = (body.get("participant_name") or "").strip()
    organization_name = (body.get("organization_name") or "").strip()
    if not participant_name:
        return jsonify({"error": "participant_name is required."}), 400
    try:
        count = timetable_service.delete_participant(
            g.current_user["id"], participant_name, organization_name
        )
        return jsonify({"deleted": count, "participant": participant_name})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@timetable_bp.route("/delete-organization", methods=["DELETE"])
@token_required
def delete_organization():
    """
    Delete ALL timetable entries for an organization or sheet.
    Body: { "organization_name": str }
    """
    body = request.get_json(silent=True) or {}
    organization_name = (body.get("organization_name") or "").strip()
    try:
        count = timetable_service.delete_organization(g.current_user["id"], organization_name)
        return jsonify({"deleted": count, "organization": organization_name})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@timetable_bp.route("/clear-all", methods=["DELETE"])
@token_required
def clear_all_timetables():
    """Delete ALL timetable entries for the current user."""
    try:
        count = timetable_service.delete_all_entries(g.current_user["id"])
        return jsonify({"deleted": count})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@timetable_bp.route("/upload", methods=["POST"])
@token_required
def upload():
    """
    Upload a CSV or Excel (.xlsx) file of timetable entries.
    Field names (case-insensitive, any order):
      participant_name, participant_email, department, class_or_team,
      organization_type, day, start_time, end_time, subject_or_task, room
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded. Send as multipart/form-data with key 'file'."}), 400

    f = request.files["file"]
    filename = (f.filename or "").lower()
    file_bytes = f.read()

    try:
        if filename.endswith(".csv"):
            rows = timetable_service.parse_csv(file_bytes)
        elif filename.endswith((".xlsx", ".xls")):
            try:
                import openpyxl
            except ImportError:
                return jsonify({"error": "openpyxl is required for Excel uploads. Install it with: pip install openpyxl"}), 500
            rows = _parse_excel(file_bytes)
        else:
            return jsonify({"error": "Only .csv and .xlsx files are supported."}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not parse file: {exc}"}), 400

    if not rows:
        return jsonify({"error": "The file is empty or has no data rows."}), 400

    result = timetable_service.bulk_upload(
        g.current_user["id"], rows,
        organization_type_override=request.form.get("organization_type") or "",
        organization_name_override=request.form.get("organization_name") or ""
    )
    # Include first few error details so the user knows what to fix
    response = {
        "inserted": result["inserted"],
        "skipped":  result["skipped"],
        "errors":   result["errors"][:5],   # cap at 5 to keep response small
        "total_errors": len(result["errors"]),
        "message": (
            f"{result['inserted']} row(s) inserted, {result['skipped']} duplicate(s) skipped"
            + (f", {len(result['errors'])} error(s)" if result["errors"] else "")
        )
    }
    return jsonify(response), 201 if result["inserted"] > 0 else 200


def _parse_excel(file_bytes: bytes) -> list:
    import openpyxl
    from datetime import time as dt_time, datetime as dt_datetime
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # Normalise headers: lowercase, strip, replace spaces/hyphens/slashes with _
    raw_headers = rows[0]
    headers = [
        str(h).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_") if h else ""
        for h in raw_headers
    ]

    # Flexible header aliases — map common variations to canonical field names
    ALIASES = {
        # participant_name
        "name": "participant_name", "participant": "participant_name",
        "student_name": "participant_name", "teacher_name": "participant_name",
        "faculty_name": "participant_name", "employee_name": "participant_name",
        # participant_email
        "email": "participant_email", "mail": "participant_email",
        # department
        "dept": "department", "dept.": "department",
        # class_or_team
        "class": "class_or_team", "team": "class_or_team", "section": "class_or_team",
        "group": "class_or_team", "batch": "class_or_team",
        "class_team": "class_or_team",   # from "Class/Team" after / → _
        # day
        "weekday": "day",
        # start_time
        "start": "start_time", "from": "start_time", "from_time": "start_time",
        "begin": "start_time", "begin_time": "start_time",
        # end_time
        "end": "end_time", "to": "end_time", "to_time": "end_time",
        "finish": "end_time", "finish_time": "end_time",
        # subject_or_task
        "subject": "subject_or_task", "task": "subject_or_task",
        "course": "subject_or_task", "module": "subject_or_task",
        "activity": "subject_or_task", "period": "subject_or_task",
        "lesson": "subject_or_task", "subject_task": "subject_or_task",  # from "Subject/Task"
        # room
        "venue": "room", "location": "room", "classroom": "room",
        "hall": "room", "lab": "room",
        # organization_type
        "org_type": "organization_type", "organization": "organization_type",
        "type": "organization_type",
    }
    headers = [ALIASES.get(h, h) for h in headers]

    def _cell_to_str(val) -> str:
        """Convert any Excel cell value to a clean string."""
        if val is None:
            return ""
        # Excel time stored as datetime.time object
        if isinstance(val, dt_time):
            return val.strftime("%H:%M")
        # Excel datetime — extract time part only
        if isinstance(val, dt_datetime):
            return val.strftime("%H:%M")
        # Excel stores times as fractions of a day (0.0–1.0)
        v = str(val).strip()
        try:
            f = float(v)
            if 0.0 <= f < 1.0:
                total_min = round(f * 24 * 60)
                return f"{total_min // 60:02d}:{total_min % 60:02d}"
        except (ValueError, TypeError):
            pass
        return v

    result = []
    for row in rows[1:]:
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        result.append({
            headers[i]: _cell_to_str(v)
            for i, v in enumerate(row)
            if i < len(headers) and headers[i]
        })
    return result


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------
@timetable_bp.route("/manual", methods=["POST"])
@token_required
def manual_entry():
    """Add a single timetable entry manually."""
    data = request.get_json(silent=True) or {}
    try:
        entry = timetable_service.create_entry(g.current_user["id"], data)
        return jsonify(entry), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        msg = str(exc)
        if "uq_timetable_slot" in msg or "duplicate" in msg.lower():
            return jsonify({"error": "A duplicate entry already exists for this participant/day/time."}), 409
        return jsonify({"error": msg}), 500


# ---------------------------------------------------------------------------
# List entries
# ---------------------------------------------------------------------------
@timetable_bp.route("", methods=["GET"])
@token_required
def list_entries():
    """
    GET /api/timetable
    Optional query params: day, department, class_or_team, participant
    """
    filters = {
        "organization_name": request.args.get("organization_name"),
        "organization_type": request.args.get("organization_type"),
        "day":               request.args.get("day"),
        "department":        request.args.get("department"),
        "class_or_team":     request.args.get("class_or_team"),
        "participant":       request.args.get("participant"),
    }
    entries = timetable_service.list_entries(g.current_user["id"], filters)
    return jsonify({"entries": entries, "total": len(entries)})


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@timetable_bp.route("/update/<entry_id>", methods=["PUT"])
@token_required
def update_entry(entry_id):
    data = request.get_json(silent=True) or {}
    try:
        updated = timetable_service.update_entry(g.current_user["id"], entry_id, data)
        return jsonify(updated)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@timetable_bp.route("/delete/<entry_id>", methods=["DELETE"])
@token_required
def delete_entry(entry_id):
    try:
        timetable_service.delete_entry(g.current_user["id"], entry_id)
        return jsonify({"message": "Entry deleted."})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Find common slot (AI-powered)
# ---------------------------------------------------------------------------
@timetable_bp.route("/find-common-slot", methods=["POST"])
@token_required
def find_common_slot():
    """
    Body: {
      "participants": ["Name or email", ...],
      "duration_minutes": 60,
      "timezone": "Asia/Kolkata",
      "work_start": "09:00",
      "work_end": "18:00",
      "skip_lunch": true,
      "skip_weekends": true,
      "search_days": 14
    }
    Returns top 3–5 slots with AI explanations.
    """
    user = g.current_user
    body = request.get_json(silent=True) or {}
    participants = body.get("participants") or []
    if not participants:
        return jsonify({"error": "At least one participant is required."}), 400

    duration = int(body.get("duration_minutes") or 60)
    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"
    organization_name = (body.get("organization_name") or "").strip()
    organization_type = (body.get("organization_type") or "").strip().lower()

    org_filter = {}
    if organization_name:
        org_filter["organization_name"] = organization_name
    elif organization_type:
        org_filter["organization_type"] = organization_type

    all_entries = timetable_service.list_entries(user["id"], org_filter or None)
    p_lower = [p.lower() for p in participants]
    relevant_entries = [
        e for e in all_entries
        if e["participant_name"].lower() in p_lower
        or e.get("participant_email", "").lower() in p_lower
    ]

    # Fetch Google Calendar events for each participant that has an email
    calendar_events: dict[str, list] = {}
    try:
        access_token = google_auth_service.get_valid_access_token(user)
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        end = now + timedelta(days=int(body.get("search_days") or 14))
        # We only have access to the organizer's own calendar — fetch it once
        events = cal_service.list_events(access_token, now.isoformat(), end.isoformat(), max_results=200)
        calendar_events[user.get("email", "me")] = events
    except Exception:
        calendar_events = {}

    slots = timetable_service.find_common_slots(
        participants=participants,
        timetable_entries=relevant_entries,
        calendar_events_by_participant=calendar_events,
        duration_minutes=duration,
        timezone_name=timezone_name,
        work_start=body.get("work_start") or "09:00",
        work_end=body.get("work_end") or "18:00",
        skip_lunch=bool(body.get("skip_lunch", True)),
        lunch_start=body.get("lunch_start") or "12:00",
        lunch_end=body.get("lunch_end") or "13:00",
        skip_weekends=bool(body.get("skip_weekends", True)),
        search_days=int(body.get("search_days") or 14),
    )

    # Add AI explanations
    try:
        slots = ai_service.explain_timetable_slots(slots, participants)
    except Exception:
        pass

    return jsonify({"slots": slots, "participants": participants, "duration_minutes": duration})


# ---------------------------------------------------------------------------
# Check conflicts
# ---------------------------------------------------------------------------
@timetable_bp.route("/check-conflicts", methods=["POST"])
@token_required
def check_conflicts():
    """
    Body: {
      "participants": [...],
      "date": "YYYY-MM-DD",
      "start_time": "HH:MM",
      "end_time": "HH:MM"
    }
    """
    body = request.get_json(silent=True) or {}
    participants = body.get("participants") or []
    date_str = body.get("date") or ""
    start_time = body.get("start_time") or ""
    end_time = body.get("end_time") or ""

    if not (participants and date_str and start_time and end_time):
        return jsonify({"error": "participants, date, start_time, and end_time are required."}), 400

    organization_name = (body.get("organization_name") or "").strip()
    organization_type = (body.get("organization_type") or "").strip().lower()
    org_filter = {}
    if organization_name:
        org_filter["organization_name"] = organization_name
    elif organization_type:
        org_filter["organization_type"] = organization_type

    all_entries = timetable_service.list_entries(g.current_user["id"], org_filter or None)
    p_lower = [p.lower() for p in participants]
    relevant_entries = [
        e for e in all_entries
        if e["participant_name"].lower() in p_lower
        or e.get("participant_email", "").lower() in p_lower
    ]

    result = timetable_service.check_conflicts(participants, relevant_entries, date_str, start_time, end_time)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Schedule meeting from chosen slot
# ---------------------------------------------------------------------------
@timetable_bp.route("/schedule", methods=["POST"])
@token_required
def schedule_from_slot():
    """
    Schedule a Google Calendar meeting from a slot chosen via the timetable.

    Body: {
      "title": str,
      "description": str,
      "date": "YYYY-MM-DD",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "timezone": str,
      "participants": ["email@..."],
      "force": false
    }
    """
    user = g.current_user
    body = request.get_json(silent=True) or {}

    required = ["title", "date", "start_time", "end_time"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    # Calculate duration from start/end
    from services.timetable_service import _time_to_minutes
    duration = _time_to_minutes(body["end_time"]) - _time_to_minutes(body["start_time"])
    if duration <= 0:
        return jsonify({"error": "end_time must be after start_time."}), 400

    meeting_data = {
        "title": body["title"],
        "description": body.get("description") or "",
        "date": body["date"],
        "start_time": body["start_time"],
        "duration_minutes": duration,
        "timezone": body.get("timezone") or user.get("timezone") or "UTC",
        "participants": body.get("participants") or [],
        "force": bool(body.get("force", False)),
    }

    # Auto-generate AI email body for the invite
    try:
        draft = ai_service.generate_email_draft(meeting_data, user)
        meeting_data["email_body"] = draft.get("body") or ""
    except Exception:
        pass

    try:
        meeting = meeting_service.create_meeting(user, meeting_data)
        return jsonify({"meeting": meeting}), 201
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc), **exc.payload}), exc.status_code
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
