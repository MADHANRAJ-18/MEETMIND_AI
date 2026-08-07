"""
AI assistant routes:
  POST /api/ai/parse-request     -> extract structured meeting fields from text
  POST /api/ai/check-slots        -> check a specific slot against the calendar
  POST /api/ai/resolve-conflict   -> AI-ranked alternative slots for a conflict
  POST /api/ai/schedule-meeting    -> end-to-end: text -> parse -> check -> create
  POST /api/ai/chat                -> conversational chat widget endpoint
"""

from datetime import datetime, timezone as dt_timezone
from flask import Blueprint, request, jsonify, g

from middleware.auth_middleware import token_required
from services import ai_service
from services import scheduler_service
from services import google_auth_service
from services import meeting_service

ai_bp = Blueprint("ai_bp", __name__, url_prefix="/api/ai")

def _resolve_participants_from_contacts(message: str, parsed_participants: list, contacts: list) -> list:
    resolved = []
    seen = set()

    def add(email):
        email = (email or "").strip().lower()
        if email and "@" in email and email not in seen:
            seen.add(email)
            resolved.append(email)

    for email in parsed_participants or []:
        add(email)

    message_l = (message or "").lower()
    for contact in contacts or []:
        name = (contact.get("name") or "").strip()
        email = (contact.get("email") or "").strip().lower()
        if not email:
            continue
        name_l = name.lower()
        first = name_l.split()[0] if name_l else ""
        matched = email in message_l or (name_l and name_l in message_l)
        if not matched and first and len(first) >= 3:
            matched = any(token.strip(".,;:!?()[]{}") == first for token in message_l.split())
        if matched:
            add(email)

    return resolved
@ai_bp.route("/parse-request", methods=["POST"])
@token_required
def parse_request():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    if not text:
        return jsonify({"error": "text is required"}), 400

    reference_date = body.get("reference_date") or datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")
    default_timezone = body.get("timezone") or user.get("timezone") or "UTC"

    try:
        parsed = ai_service.parse_meeting_request(text, reference_date, default_timezone)
        return jsonify({"parsed": parsed})
    except ai_service.AIServiceError as exc:
        return jsonify({"error": str(exc)}), 502


@ai_bp.route("/check-slots", methods=["POST"])
@token_required
def check_slots():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    required = ["date", "time", "duration_minutes"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        result = scheduler_service.check_conflict_and_suggest(
            access_token, body["date"], body["time"], int(body["duration_minutes"]), timezone_name
        )
        return jsonify(result)
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ai_bp.route("/resolve-conflict", methods=["POST"])
@token_required
def resolve_conflict_route():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    required = ["date", "time", "duration_minutes"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"

    try:
        access_token = google_auth_service.get_valid_access_token(user)
        check = scheduler_service.check_conflict_and_suggest(
            access_token, body["date"], body["time"], int(body["duration_minutes"]), timezone_name
        )
        ai_resolution = ai_service.resolve_conflict(
            check["requested"], check["busy_periods"], check["alternatives"]
        )
        return jsonify({"availability": check, "ai_resolution": ai_resolution})
    except ai_service.AIServiceError as exc:
        return jsonify({"error": str(exc)}), 502
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ai_bp.route("/schedule-meeting", methods=["POST"])
@token_required
def ai_schedule_meeting():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    if not text:
        return jsonify({"error": "text is required"}), 400

    reference_date = body.get("reference_date") or datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")
    default_timezone = body.get("timezone") or user.get("timezone") or "UTC"
    contacts = body.get("participants_context") or []

    try:
        parsed = ai_service.parse_meeting_request(text, reference_date, default_timezone)
    except ai_service.AIServiceError as exc:
        return jsonify({"error": str(exc)}), 502

    if not parsed.get("date") or not parsed.get("time"):
        return jsonify(
            {
                "error": "Could not determine a date/time from the request. "
                "Try again with a more specific time, or use /api/meetings directly.",
                "parsed": parsed,
            }
        ), 422

    meeting_payload = {
        "title": parsed.get("title") or "Untitled meeting",
        "description": parsed.get("description") or "",
        "date": parsed["date"],
        "start_time": parsed["time"],
        "duration_minutes": parsed.get("duration_minutes", 30),
        "timezone": parsed.get("timezone") or default_timezone,
        "participants": _resolve_participants_from_contacts(text, parsed.get("participants", []), contacts),
        "force": bool(body.get("force", False)),
    }

    try:
        meeting = meeting_service.create_meeting(user, meeting_payload)
        return jsonify({"parsed": parsed, "meeting": meeting}), 201
    except meeting_service.MeetingServiceError as exc:
        return jsonify({"error": str(exc), **exc.payload, "parsed": parsed}), exc.status_code
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ai_bp.route("/chat", methods=["POST"])
@token_required
def chat():
    """
    Single endpoint for the chat widget. Classifies what the user wants, then
    runs the matching action (show free slots / resolve conflict / create a
    meeting / just talk), and returns a chat-ready reply plus structured data
    the frontend can render (slot chips, the created meeting, etc).

    Body: { "message": str, "history": [{"role","content"}], "force": bool (optional),
            "reference_date": "YYYY-MM-DD" (optional), "timezone": str (optional) }

    Response: { "intent": str, "reply": str, "data": {...} }
    """
    user = g.current_user
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    history = body.get("history") or []
    reference_date = body.get("reference_date") or datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")
    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"
    force = bool(body.get("force", False))
    contacts = body.get("participants_context") or []

    try:
        intent_result = ai_service.classify_chat_intent(message, reference_date, timezone_name, history)
    except ai_service.AIServiceError as exc:
        return jsonify({"error": str(exc)}), 502

    intent = intent_result.get("intent") or "general"

    # --- general chit-chat: no calendar access needed ---------------------
    if intent == "general":
        reply = ai_service.generate_chat_reply("general", {"message": message}) or (
            "I can help you find free time, sort out a scheduling conflict, "
            "or get a meeting on the calendar - just tell me what you need."
        )
        return jsonify({"intent": intent, "reply": reply, "data": {}})

    # Everything else needs a valid Google access token.
    try:
        access_token = google_auth_service.get_valid_access_token(user)
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401

    duration = int(intent_result.get("duration_minutes") or 30)
    date_str = intent_result.get("date") or reference_date
    tz_name = intent_result.get("timezone") or timezone_name

    # --- show free slots ---------------------------------------------------
    if intent == "show_free_slots":
        try:
            result = scheduler_service.find_open_slots(access_token, date_str, duration, tz_name)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        reply = ai_service.generate_chat_reply("show_free_slots", result) or (
            "Here are some open slots I found." if result["free_slots"] else
            "I couldn't find any open slots in that window."
        )
        return jsonify({"intent": intent, "reply": reply, "data": result})

    # --- resolve a conflict at a specific requested time --------------------
    if intent == "resolve_conflict":
        time_str = intent_result.get("time")
        if not time_str:
            reply = "What time were you thinking of? Give me a date and time and I'll check it."
            return jsonify({"intent": intent, "reply": reply, "data": {}})
        try:
            check = scheduler_service.check_conflict_and_suggest(access_token, date_str, time_str, duration, tz_name)
            ai_resolution = None
            if check["conflict"]:
                ai_resolution = ai_service.resolve_conflict(
                    check["requested"], check["busy_periods"], check["alternatives"]
                )
        except ai_service.AIServiceError as exc:
            return jsonify({"error": str(exc)}), 502
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        payload = {"availability": check, "ai_resolution": ai_resolution}
        reply = ai_service.generate_chat_reply("resolve_conflict", payload) or (
            "That time is free - no conflicts." if not check["conflict"] else
            "That time conflicts with something on your calendar."
        )
        return jsonify({"intent": intent, "reply": reply, "data": payload})

    # --- create a meeting ----------------------------------------------------
    if intent == "create_meeting":
        if not intent_result.get("date") or not intent_result.get("time"):
            reply = "Sure - what date and time should I book this for?"
            return jsonify({"intent": intent, "reply": reply, "data": {"parsed": intent_result}})

        resolved_participants = _resolve_participants_from_contacts(
            message, intent_result.get("participants", []), contacts
        )
        if not resolved_participants:
            reply = (
                "Who should I invite to this meeting? "
                "Pick from your contacts below or type an email address."
            )
            return jsonify({
                "intent": "needs_participants",
                "reply": reply,
                "data": {"parsed": intent_result}
            })

        # ------------------------------------------------------------------
        # Step 1: necessity check (skip if user already confirmed "book anyway")
        # ------------------------------------------------------------------
        if not force:
            try:
                existing_count = scheduler_service.count_meetings_on_date(
                    access_token, intent_result["date"], tz_name
                )
            except Exception:
                existing_count = 0

            necessity = ai_service.check_meeting_necessity(
                title=intent_result.get("title") or "",
                description=intent_result.get("description") or "",
                duration_minutes=duration,
                existing_meetings_on_day=existing_count,
            )
            verdict = necessity.get("verdict", "necessary")

            # Overload: day already has 4+ meetings
            if verdict == "overload_risk" or (existing_count >= 4 and verdict == "necessary"):
                try:
                    alt_slots = scheduler_service.find_open_slots(
                        access_token, date_str, duration, tz_name,
                        search_days=7, max_slots=3
                    )
                    alternatives = alt_slots.get("free_slots", [])
                except Exception:
                    alternatives = []

                reply = (
                    f"You already have {existing_count} meetings on "
                    f"{intent_result['date']}. That's a heavy day — "
                    f"here are some lighter slots nearby. Want me to book one of these instead, "
                    f"or book the original time anyway?"
                )
                return jsonify({
                    "intent": "overload_warning",
                    "reply": reply,
                    "data": {
                        "parsed": intent_result,
                        "existing_count": existing_count,
                        "alternatives": alternatives,
                        "original_message": message,
                    }
                })

            # Unnecessary meeting: email or async would work better
            if verdict in ("email_instead", "async_instead"):
                suggestion = necessity.get("suggestion") or ""
                reason = necessity.get("reason") or ""
                async_label = "a recorded video or shared doc" if verdict == "async_instead" else "an email"
                reply = (
                    f"Heads up — {reason} "
                    f"Sending {async_label} might save everyone's time. "
                    f"{suggestion} "
                    f"Want me to book the meeting anyway?"
                )
                return jsonify({
                    "intent": "necessity_warning",
                    "reply": reply,
                    "data": {
                        "verdict": verdict,
                        "reason": reason,
                        "suggestion": suggestion,
                        "parsed": intent_result,
                        "original_message": message,
                    }
                })

        # ------------------------------------------------------------------
        # Step 2: all checks passed (or user said force) — create the meeting
        # ------------------------------------------------------------------
        meeting_payload = {
            "title": intent_result.get("title") or "Untitled meeting",
            "description": intent_result.get("description") or "",
            "date": intent_result["date"],
            "start_time": intent_result["time"],
            "duration_minutes": duration,
            "timezone": tz_name,
            "participants": resolved_participants,
            "force": force,
        }

        # Auto-generate a personalized email body for the Calendar invite
        try:
            draft = ai_service.generate_email_draft(meeting_payload, user)
            meeting_payload["email_body"] = draft.get("body") or ""
        except Exception:
            pass  # silently skip — plain description will be used instead

        try:
            meeting = meeting_service.create_meeting(user, meeting_payload)
        except meeting_service.MeetingServiceError as exc:
            payload = {"error": str(exc), **exc.payload, "parsed": intent_result}
            reply = ai_service.generate_chat_reply("create_meeting_conflict", payload) or (
                "That time conflicts with something on your calendar. "
                "Want me to book it anyway, or try one of the alternatives?"
            )
            return jsonify({"intent": intent, "reply": reply, "data": payload}), exc.status_code
        except google_auth_service.GoogleAuthError as exc:
            return jsonify({"error": str(exc)}), 401
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        payload = {"meeting": meeting, "parsed": intent_result}
        reply = ai_service.generate_chat_reply("create_meeting", payload) or (
            f"Done - \"{meeting.get('title')}\" is booked for {meeting.get('meeting_date')} at "
            f"{meeting.get('start_time')}."
        )
        return jsonify({"intent": intent, "reply": reply, "data": payload}), 201

    # Fallback - treat unknown intents as general chat.
    reply = "I can help you find free time, resolve a conflict, or create a meeting - what would you like to do?"
    return jsonify({"intent": "general", "reply": reply, "data": {}})


@ai_bp.route("/draft-email", methods=["POST"])
@token_required
def draft_email():
    """
    Generate an AI-written meeting invitation email draft.

    Body: {
      "meeting": { title, date, start_time, duration_minutes, timezone,
                   description, meeting_link, participants }
    }
    Response: { "subject": str, "body": str }
    """
    user = g.current_user
    body = request.get_json(silent=True) or {}
    meeting = body.get("meeting") or {}

    if not meeting.get("title"):
        return jsonify({"error": "meeting.title is required"}), 400

    try:
        draft = ai_service.generate_email_draft(meeting, user)
        return jsonify(draft)
    except ai_service.AIServiceError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@ai_bp.route("/optimize", methods=["POST"])
@token_required
def optimize():
    """
    Analyze the user's calendar for the next 14 days and return AI-generated
    suggestions for a more productive meeting schedule.

    Body: { "timezone": str (optional, falls back to user's stored timezone) }

    Response: {
      "analysis": { overloaded_days, light_days, avg_meetings_per_day,
                    busiest_day, lightest_day, total_meeting_hours },
      "suggestions": [ { id, type, title, explanation, from_date, to_date,
                         meeting_title, event_id, productivity_gain } ],
      "overall_productivity_gain": number,
      "summary": str,
      "day_summaries": { date: { count, total_minutes, meetings } }
    }
    """
    from services import optimizer_service

    user = g.current_user
    body = request.get_json(silent=True) or {}
    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"

    try:
        access_token = google_auth_service.get_valid_access_token(user)
    except google_auth_service.GoogleAuthError as exc:
        return jsonify({"error": str(exc)}), 401

    try:
        result = optimizer_service.analyze_and_suggest(access_token, timezone_name)
        return jsonify(result)
    except ai_service.AIServiceError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ai_bp.route("/send-skip-email", methods=["POST"])
@token_required
def send_skip_email():
    """
    Sends a plain email instead of booking a meeting (necessity-checker flow).
    Generates a draft if subject/body not provided, then sends immediately.

    Body: {
      "meeting": { title, description, date, start_time, duration_minutes, timezone },
      "recipients": ["email1", "email2"],
      "subject": str (optional — AI generates if omitted),
      "body":    str (optional — AI generates if omitted)
    }
    Response: { "sent": bool, "method": str, "subject": str, "body": str }
    """
    from services import email_service

    user = g.current_user
    body = request.get_json(silent=True) or {}
    meeting = body.get("meeting") or {}
    recipients = [r for r in (body.get("recipients") or []) if r]
    custom_subject = (body.get("subject") or "").strip()
    custom_body = (body.get("body") or "").strip()

    if not recipients:
        return jsonify({"error": "At least one recipient is required"}), 400

    # Generate draft if subject/body not provided
    if not custom_subject or not custom_body:
        try:
            draft = ai_service.generate_email_draft(meeting, user)
            custom_subject = custom_subject or draft.get("subject") or f"Re: {meeting.get('title', 'Update')}"
            custom_body = custom_body or draft.get("body") or ""
        except Exception:
            custom_subject = custom_subject or f"Re: {meeting.get('title', 'Update')}"
            custom_body = custom_body or f"Hi,\n\nJust a quick update regarding {meeting.get('title', 'our topic')}.\n\nBest,\n{user.get('name', '')}"

    try:
        access_token = google_auth_service.get_valid_access_token(user)
    except google_auth_service.GoogleAuthError:
        access_token = None

    result = email_service.send_plain_email(
        sender_email=user.get("email", ""),
        recipients=recipients,
        subject=custom_subject,
        body=custom_body,
        access_token=access_token,
    )
    result["subject"] = custom_subject
    result["body"] = custom_body
    return jsonify(result), (200 if result.get("sent") else 502)


@ai_bp.route("/check-necessity", methods=["POST"])
@token_required
def check_necessity():
    """
    Checks if a proposed meeting is actually necessary.
    Called by the schedule form before booking.

    Body: {
      "title": str,
      "description": str,
      "duration_minutes": int,
      "date": "YYYY-MM-DD",
      "timezone": str
    }
    Response: { "verdict": str, "reason": str, "suggestion": str, "confidence": float }
    """
    from services import scheduler_service

    user = g.current_user
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    duration = int(body.get("duration_minutes") or 30)
    date_str = body.get("date") or ""
    timezone_name = body.get("timezone") or user.get("timezone") or "UTC"

    if not title:
        return jsonify({"verdict": "necessary", "reason": "", "suggestion": None, "confidence": 1.0})

    # Count existing meetings on that day from real calendar
    existing_count = 0
    if date_str:
        try:
            access_token = google_auth_service.get_valid_access_token(user)
            existing_count = scheduler_service.count_meetings_on_date(
                access_token, date_str, timezone_name
            )
        except Exception:
            existing_count = 0

    result = ai_service.check_meeting_necessity(
        title=title,
        description=description,
        duration_minutes=duration,
        existing_meetings_on_day=existing_count,
    )
    result["existing_count"] = existing_count
    return jsonify(result)
