"""Email notifications for meeting invitations."""

import base64
from email.message import EmailMessage
import smtplib
import ssl

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config.settings import settings


def email_enabled() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD)


def _smtp_password() -> str:
    return (settings.SMTP_PASSWORD or "").replace(" ", "").strip()


def _build_message(meeting: dict, organizer: dict, recipient: str,
                   custom_subject: str = "", custom_body: str = "") -> EmailMessage:
    sender = settings.SMTP_FROM or settings.SMTP_USER or organizer.get("email") or "me"
    title = meeting.get("title") or "Meeting invitation"
    date = meeting.get("meeting_date") or meeting.get("date") or ""
    start = str(meeting.get("start_time") or "")[:5]
    timezone = meeting.get("timezone") or "UTC"
    meet_link = meeting.get("meeting_link") or ""
    organizer_name = organizer.get("name") or organizer.get("email") or "MeetMind"

    if custom_subject and custom_body:
        subject = custom_subject
        body = custom_body
    else:
        body_lines = [
            f"{organizer_name} invited you to a meeting.",
            "",
            f"Title: {title}",
            f"Date: {date}",
            f"Time: {start} ({timezone})",
        ]
        if meet_link:
            body_lines.extend(["", f"Google Meet: {meet_link}"])
        body_lines.extend(["", "Please accept the Google Calendar invitation so MeetMind can mark this meeting as approved."])
        subject = f"Meeting invite: {title}"
        body = "\n".join(body_lines)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)
    return msg


def _send_via_smtp(messages: list[EmailMessage]) -> None:
    if not email_enabled():
        raise RuntimeError("SMTP not configured")
    if settings.SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
    with smtp:
        if settings.SMTP_USE_TLS and settings.SMTP_PORT != 465:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(settings.SMTP_USER.strip(), _smtp_password())
        for msg in messages:
            smtp.send_message(msg)


def _send_via_gmail_api(messages: list[EmailMessage], access_token: str) -> None:
    if not access_token:
        raise RuntimeError("Google access token missing")
    service = build("gmail", "v1", credentials=Credentials(token=access_token), cache_discovery=False)
    for msg in messages:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(userId="me", body={"raw": raw}).execute()


def send_meeting_invites(meeting: dict, organizer: dict, access_token: str | None = None,
                         custom_subject: str = "", custom_body: str = "") -> dict:
    participants = [email for email in (meeting.get("participants") or []) if email]
    if not participants:
        return {"sent": False, "error": "No participant email found for this meeting."}

    messages = [
        _build_message(meeting, organizer, recipient, custom_subject, custom_body)
        for recipient in participants
    ]
    errors = []

    try:
        _send_via_smtp(messages)
        return {"sent": True, "method": "smtp"}
    except Exception as exc:
        errors.append(f"SMTP: {exc}")

    try:
        _send_via_gmail_api(messages, access_token)
        return {"sent": True, "method": "gmail_api"}
    except Exception as exc:
        errors.append(f"Gmail API: {exc}")

    return {"sent": False, "error": " | ".join(errors)}


def send_plain_email(
    sender_email: str,
    recipients: list[str],
    subject: str,
    body: str,
    access_token: str | None = None,
) -> dict:
    """
    Send a plain-text email to one or more recipients.
    Used by the necessity checker's 'skip the meeting, send email instead' flow.
    Tries SMTP first, falls back to Gmail API.
    """
    if not recipients:
        return {"sent": False, "error": "No recipients provided."}

    sender = settings.SMTP_FROM or settings.SMTP_USER or sender_email or "me"
    messages = []
    for recipient in recipients:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content(body)
        messages.append(msg)

    errors = []
    try:
        _send_via_smtp(messages)
        return {"sent": True, "method": "smtp"}
    except Exception as exc:
        errors.append(f"SMTP: {exc}")

    try:
        _send_via_gmail_api(messages, access_token)
        return {"sent": True, "method": "gmail_api"}
    except Exception as exc:
        errors.append(f"Gmail API: {exc}")

    return {"sent": False, "error": " | ".join(errors)}
