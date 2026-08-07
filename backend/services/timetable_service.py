"""
Smart Timetable Scheduler — service layer.

Handles CRUD for the `timetables` table and the core slot-finding logic that
combines timetable data with live Google Calendar free/busy data.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, date as date_type
from zoneinfo import ZoneInfo

from config.supabase_client import get_supabase

# ---------------------------------------------------------------------------
# DAYS
# ---------------------------------------------------------------------------
DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_TO_WEEKDAY = {d: i for i, d in enumerate(DAYS_ORDER)}   # Monday=0 … Sunday=6


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

def _parse_time_str(val: str) -> tuple[int, int]:
    """Parse 'H:MM', 'HH:MM', or 'HH:MM:SS' into (hour, minute). Raises ValueError on failure."""
    val = val.strip()
    parts = val.split(":")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse time '{val}'. Use HH:MM format.")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Cannot parse time '{val}'. Use HH:MM format.")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Time '{val}' is out of range.")
    return h, m


def _validate_row(row: dict) -> dict:
    """Raise ValueError if required fields are missing or times are invalid."""
    required = ["participant_name", "day", "start_time", "end_time", "subject_or_task"]
    missing = [f for f in required if not (row.get(f) or "").strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    day = row["day"].strip().capitalize()
    if day not in DAYS_ORDER:
        raise ValueError(f"Invalid day '{day}'. Must be one of {DAYS_ORDER}.")

    sh, sm = _parse_time_str(row["start_time"])
    eh, em = _parse_time_str(row["end_time"])
    start_mins = sh * 60 + sm
    end_mins   = eh * 60 + em

    if start_mins >= end_mins:
        raise ValueError(
            f"start_time ({row['start_time']}) must be before end_time ({row['end_time']})."
        )

    return {
        "participant_name":  row.get("participant_name", "").strip(),
        "participant_email": (row.get("participant_email") or "").strip().lower(),
        "department":        (row.get("department") or "").strip(),
        "class_or_team":     (row.get("class_or_team") or "").strip(),
        "organization_type": (row.get("organization_type") or "company").strip().lower(),
        "organization_name": (row.get("organization_name") or "").strip(),
        "day":               day,
        "start_time":        f"{sh:02d}:{sm:02d}",
        "end_time":          f"{eh:02d}:{em:02d}",
        "subject_or_task":   row.get("subject_or_task", "").strip(),
        "room":              (row.get("room") or "").strip(),
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_entries(user_id: str, filters: dict | None = None) -> list:
    sb = get_supabase()
    q = sb.table("timetables").select("*").eq("created_by", user_id)
    if filters:
        if filters.get("organization_name"):
            q = q.eq("organization_name", filters["organization_name"])
        elif filters.get("organization_type"):
            q = q.eq("organization_type", filters["organization_type"])
        if filters.get("day"):
            q = q.eq("day", filters["day"])
        if filters.get("department"):
            q = q.ilike("department", f"%{filters['department']}%")
        if filters.get("class_or_team"):
            q = q.ilike("class_or_team", f"%{filters['class_or_team']}%")
        if filters.get("participant"):
            q = q.ilike("participant_name", f"%{filters['participant']}%")
    result = q.order("day").order("start_time").execute()
    return result.data or []


def list_organization_names(user_id: str) -> list[dict]:
    """Return all distinct organization names and types uploaded by this user."""
    sb = get_supabase()
    result = sb.table("timetables").select("organization_name,organization_type").eq("created_by", user_id).execute()
    seen = {}
    for row in (result.data or []):
        name = row.get("organization_name") or row.get("organization_type") or ""
        if name and name not in seen:
            seen[name] = row.get("organization_type") or "company"
    return [{"name": k, "type": v} for k, v in seen.items()]


def create_entry(user_id: str, data: dict) -> dict:
    validated = _validate_row(data)
    validated["organization_name"] = (data.get("organization_name") or "").strip()
    validated["created_by"] = user_id
    sb = get_supabase()
    result = sb.table("timetables").insert(validated).execute()
    return result.data[0]


def update_entry(user_id: str, entry_id: str, data: dict) -> dict:
    validated = _validate_row(data)
    sb = get_supabase()
    existing = sb.table("timetables").select("id").eq("id", entry_id).eq("created_by", user_id).execute()
    if not existing.data:
        raise ValueError("Entry not found or access denied.")
    result = sb.table("timetables").update(validated).eq("id", entry_id).execute()
    return result.data[0]


def delete_entry(user_id: str, entry_id: str) -> None:
    sb = get_supabase()
    existing = sb.table("timetables").select("id").eq("id", entry_id).eq("created_by", user_id).execute()
    if not existing.data:
        raise ValueError("Entry not found or access denied.")
    sb.table("timetables").delete().eq("id", entry_id).execute()


def delete_participant(user_id: str, participant_name: str, organization_name: str = "") -> int:
    """Delete all timetable entries for one participant within an organization."""
    sb = get_supabase()
    q = sb.table("timetables").delete().eq("created_by", user_id).eq("participant_name", participant_name)
    if organization_name:
        q = q.eq("organization_name", organization_name)
    result = q.execute()
    return len(result.data or [])


def delete_organization(user_id: str, organization_name: str) -> int:
    """Delete ALL timetable entries for an entire organization (one Excel upload batch)."""
    sb = get_supabase()
    q = sb.table("timetables").delete().eq("created_by", user_id)
    if organization_name:
        q = q.eq("organization_name", organization_name)
    result = q.execute()
    return len(result.data or [])


def delete_all_entries(user_id: str) -> int:
    """Delete ALL timetable entries uploaded by this user."""
    sb = get_supabase()
    result = sb.table("timetables").delete().eq("created_by", user_id).execute()
    return len(result.data or [])



# ---------------------------------------------------------------------------
# BULK UPLOAD (CSV / Excel rows already parsed to list[dict])
# ---------------------------------------------------------------------------

def parse_only(rows: list[dict],
               organization_type_override: str = "",
               organization_name_override: str = "") -> dict:
    """
    Validate rows WITHOUT saving to Supabase.
    Returns { valid: [...], errors: [...] } so the frontend can show a preview.
    """
    valid, errors = [], []
    for i, raw in enumerate(rows):
        try:
            validated = _validate_row(raw)
            if organization_type_override:
                validated["organization_type"] = organization_type_override.strip().lower()
            if organization_name_override:
                validated["organization_name"] = organization_name_override.strip()
            valid.append(validated)
        except Exception as exc:
            errors.append({"row": i + 1, "error": str(exc)})
    return {"valid": valid, "errors": errors}

def bulk_upload(user_id: str, rows: list[dict],
                organization_type_override: str = "",
                organization_name_override: str = "") -> dict:
    """
    Insert multiple rows, skipping duplicates.
    organization_name_override: the user-supplied name for this upload batch
    (e.g. "Acme Corp", "CSE Dept A 2024"). Used as the isolation key so
    different organizations never merge even if they share the same type.
    """
    inserted, skipped, errors = 0, 0, []
    sb = get_supabase()

    for i, raw in enumerate(rows):
        try:
            validated = _validate_row(raw)
            if organization_type_override:
                validated["organization_type"] = organization_type_override.strip().lower()
            if organization_name_override:
                validated["organization_name"] = organization_name_override.strip()
            validated["created_by"] = user_id
            sb.table("timetables").insert(validated).execute()
            inserted += 1
        except Exception as exc:
            msg = str(exc)
            if "uq_timetable_slot" in msg or "duplicate" in msg.lower():
                skipped += 1
            else:
                errors.append({"row": i + 1, "error": msg})

    return {"inserted": inserted, "skipped": skipped, "errors": errors}


def parse_csv(file_bytes: bytes) -> list[dict]:
    """Parse CSV bytes into a list of raw row dicts."""
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


# ---------------------------------------------------------------------------
# SLOT FINDING
# ---------------------------------------------------------------------------

def _time_to_minutes(t: str) -> int:
    """'H:MM', 'HH:MM', or 'HH:MM:SS' → total minutes since midnight."""
    h, m = _parse_time_str(t)
    return h * 60 + m


def _minutes_to_time(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _busy_from_timetable(entries: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """
    Returns { participant_name: [(start_min, end_min), ...] } per weekday
    keyed by "Monday", "Tuesday", etc.
    """
    busy: dict[str, dict[str, list]] = {}
    for e in entries:
        name = e["participant_name"]
        day = e["day"]
        if name not in busy:
            busy[name] = {}
        if day not in busy[name]:
            busy[name][day] = []
        busy[name][day].append((
            _time_to_minutes(e["start_time"]),
            _time_to_minutes(e["end_time"]),
        ))
    return busy


def _busy_from_calendar_events(events: list[dict], tz_name: str) -> dict[str, list[tuple[int, int]]]:
    """
    Convert Google Calendar events to { 'Monday': [(start_min, end_min)] }.
    """
    tz = ZoneInfo(tz_name)
    busy: dict[str, list] = {}
    for ev in events:
        start_raw = ev.get("start", {})
        end_raw   = ev.get("end", {})
        if "dateTime" not in start_raw:
            continue   # all-day
        try:
            s = datetime.fromisoformat(start_raw["dateTime"]).astimezone(tz)
            e = datetime.fromisoformat(end_raw["dateTime"]).astimezone(tz)
        except Exception:
            continue
        day_name = DAYS_ORDER[s.weekday()]
        if day_name not in busy:
            busy[day_name] = []
        busy[day_name].append((
            s.hour * 60 + s.minute,
            e.hour * 60 + e.minute,
        ))
    return busy


def _is_free(busy_slots: list[tuple[int, int]], start: int, end: int) -> bool:
    for bs, be in busy_slots:
        if start < be and end > bs:
            return False
    return True


def _detect_lunch_windows(entries: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """
    Scans timetable entries whose subject/task indicates a break period
    (lunch, break, recess, interval, free period, etc.) and returns their
    time ranges keyed by weekday name.

    Returns { "Monday": [(start_min, end_min), ...], ... }

    These are used instead of the fixed 12:00–13:00 hardcoded window so
    organizations with non-standard lunch times (e.g. 13:00–14:00 for a
    university, or 10:30–11:00 recess for a school) are handled correctly.
    """
    BREAK_KEYWORDS = {
        "lunch", "break", "recess", "interval",
        "free period", "free", "rest", "prayer",
        "snack", "recreation", "off", "gap",
    }
    windows: dict[str, list[tuple[int, int]]] = {}
    for e in entries:
        subject = (e.get("subject_or_task") or "").lower().strip()
        is_break = any(kw in subject for kw in BREAK_KEYWORDS)
        if not is_break:
            continue
        day = e.get("day", "")
        if day not in DAYS_ORDER:
            continue
        try:
            s = _time_to_minutes(e["start_time"])
            en = _time_to_minutes(e["end_time"])
        except Exception:
            continue
        if day not in windows:
            windows[day] = []
        windows[day].append((s, en))
    return windows


def find_common_slots(
    participants: list[str],
    timetable_entries: list[dict],
    calendar_events_by_participant: dict[str, list],
    duration_minutes: int,
    timezone_name: str,
    work_start: str = "09:00",
    work_end: str = "18:00",
    skip_lunch: bool = True,
    lunch_start: str = "12:00",   # kept for API compat but IGNORED when timetable data exists
    lunch_end: str = "13:00",     # kept for API compat but IGNORED when timetable data exists
    skip_weekends: bool = True,
    search_days: int = 14,
) -> list[dict]:
    """
    Find up to 5 common free slots for all participants.

    Lunch/break windows are detected from the timetable entries themselves
    (any entry whose subject contains 'lunch', 'break', 'recess', etc.).
    The fixed lunch_start/lunch_end parameters are used only as a fallback
    when no break entries are found in the timetable.
    """
    work_s = _time_to_minutes(work_start)
    work_e = _time_to_minutes(work_end)

    # Detect lunch/break windows from timetable — per day
    detected_breaks = _detect_lunch_windows(timetable_entries) if skip_lunch else {}

    # Fallback: if no break entries found in timetable AND skip_lunch is on,
    # use the provided lunch_start/lunch_end as a single global window.
    fallback_lunch_s = _time_to_minutes(lunch_start)
    fallback_lunch_e = _time_to_minutes(lunch_end)
    use_fallback_lunch = skip_lunch and not detected_breaks

    # Build timetable busy map: { participant: { day_name: [(s,e)] } }
    tt_busy = _busy_from_timetable(timetable_entries)

    # Build calendar busy map per participant per weekday
    cal_busy: dict[str, dict[str, list]] = {}
    for p, events in calendar_events_by_participant.items():
        cal_busy[p] = _busy_from_calendar_events(events, timezone_name)

    # Only search days that actually appear in the timetable.
    # If the timetable has no entries at all, fall back to all weekdays.
    timetable_days = set(e["day"] for e in timetable_entries if e.get("day"))
    if not timetable_days:
        timetable_days = set(DAYS_ORDER[:5])  # Mon–Fri fallback

    tz = ZoneInfo(timezone_name)
    today = datetime.now(tz).date()
    slots = []

    for day_offset in range(search_days):
        check_date = today + timedelta(days=day_offset)
        weekday_name = DAYS_ORDER[check_date.weekday()]

        # Skip days not in the timetable
        if weekday_name not in timetable_days:
            continue

        if skip_weekends and weekday_name in ("Saturday", "Sunday"):
            continue

        # Get break windows for this weekday
        day_breaks = detected_breaks.get(weekday_name, []) if skip_lunch else []

        # Try every 30-minute slot within working hours
        t = work_s
        while t + duration_minutes <= work_e:
            slot_s = t
            slot_e = t + duration_minutes

            # Skip timetable-detected break windows
            if day_breaks and not _is_free(day_breaks, slot_s, slot_e):
                t += 30
                continue

            # Skip fallback fixed lunch window (only when no timetable breaks found)
            if use_fallback_lunch and not (slot_e <= fallback_lunch_s or slot_s >= fallback_lunch_e):
                t += 30
                continue

            # Check every participant
            all_free = True
            available = []
            busy_participants = []

            for p in participants:
                p_key = p.lower()

                # Timetable check — match by participant name or email
                tt_match = next(
                    (k for k in tt_busy if k.lower() == p_key), None
                )
                if tt_match:
                    slots_for_day = tt_busy[tt_match].get(weekday_name, [])
                    if not _is_free(slots_for_day, slot_s, slot_e):
                        all_free = False
                        busy_participants.append(p)
                        break   # no t+=30 here — outer loop handles advancement

                # Calendar check
                cal_match = next(
                    (k for k in cal_busy if k.lower() == p_key), None
                )
                if cal_match:
                    cal_slots = cal_busy[cal_match].get(weekday_name, [])
                    if not _is_free(cal_slots, slot_s, slot_e):
                        all_free = False
                        busy_participants.append(p)
                        break

                available.append(p)

            if all_free:
                slots.append({
                    "date":          check_date.isoformat(),
                    "day":           weekday_name,
                    "start_time":    _minutes_to_time(slot_s),
                    "end_time":      _minutes_to_time(slot_e),
                    "available":     participants,
                    "available_count": len(participants),
                    "conflict_score": 0,
                })
                if len(slots) >= 5:
                    return slots
            else:
                if len(available) >= max(1, len(participants) - 1):
                    slots.append({
                        "date":          check_date.isoformat(),
                        "day":           weekday_name,
                        "start_time":    _minutes_to_time(slot_s),
                        "end_time":      _minutes_to_time(slot_e),
                        "available":     available,
                        "available_count": len(available),
                        "conflict_score": len(busy_participants),
                    })

            t += 30

    slots.sort(key=lambda s: (s["conflict_score"], s["date"], s["start_time"]))
    return slots[:5]


def check_conflicts(
    participants: list[str],
    timetable_entries: list[dict],
    date_str: str,
    start_time: str,
    end_time: str,
) -> dict:
    """
    Check a specific date+time against timetable entries.
    Returns { conflict: bool, conflicts: [{participant, reason}] }
    """
    try:
        check_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"conflict": False, "conflicts": []}

    weekday_name = DAYS_ORDER[check_date.weekday()]
    slot_s = _time_to_minutes(start_time)
    slot_e = _time_to_minutes(end_time)
    tt_busy = _busy_from_timetable(timetable_entries)

    conflicts = []
    for p in participants:
        p_key = p.lower()
        tt_match = next((k for k in tt_busy if k.lower() == p_key), None)
        if tt_match:
            for day_slots in [tt_busy[tt_match].get(weekday_name, [])]:
                for bs, be in day_slots:
                    if slot_s < be and slot_e > bs:
                        conflicts.append({
                            "participant": p,
                            "reason": (
                                f"{p} has a timetable entry on {weekday_name} "
                                f"{_minutes_to_time(bs)}–{_minutes_to_time(be)}"
                            ),
                        })

    return {"conflict": bool(conflicts), "conflicts": conflicts}
