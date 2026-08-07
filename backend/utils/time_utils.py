"""
Time helpers shared by the calendar, scheduler, and meeting services.

All Google Calendar interaction uses RFC3339 datetime strings
(e.g. "2026-06-23T10:30:00+05:30"), so these helpers center on
converting between plain date/time strings (from forms or the AI
parser) and RFC3339, plus computing free slots between busy periods.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def to_rfc3339(date_str: str, time_str: str, tz_name: str) -> str:
    """Combine 'YYYY-MM-DD' + 'HH:MM' + IANA timezone into an RFC3339 string."""
    tz = ZoneInfo(tz_name)
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    return dt.isoformat()


def add_minutes(rfc3339_str: str, minutes: int) -> str:
    dt = datetime.fromisoformat(rfc3339_str)
    return (dt + timedelta(minutes=minutes)).isoformat()


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 / ISO8601 string (handles trailing 'Z')."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def intervals_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    return start1 < end2 and start2 < end1


def find_free_slots(
    busy_periods: list,
    duration_minutes: int,
    tz_name: str,
    search_start: datetime,
    search_end: datetime,
    work_start_hour: int = 9,
    work_end_hour: int = 18,
    max_slots: int = 3,
):
    """
    Walk forward from search_start to search_end, skipping busy periods and
    out-of-hours time, returning up to max_slots (start, end) datetime tuples
    of length duration_minutes that are completely free.
    """
    tz = ZoneInfo(tz_name)

    busy = []
    for period in busy_periods:
        busy.append((parse_rfc3339(period["start"]), parse_rfc3339(period["end"])))
    busy.sort(key=lambda p: p[0])

    free_slots = []
    cursor = search_start
    safety_counter = 0

    while cursor < search_end and len(free_slots) < max_slots and safety_counter < 5000:
        safety_counter += 1
        cursor_local = cursor.astimezone(tz)

        # Skip to working hours if outside them
        if cursor_local.hour < work_start_hour:
            cursor_local = cursor_local.replace(
                hour=work_start_hour, minute=0, second=0, microsecond=0
            )
            cursor = cursor_local.astimezone(cursor.tzinfo)
            continue

        if cursor_local.hour >= work_end_hour:
            next_day_local = (cursor_local + timedelta(days=1)).replace(
                hour=work_start_hour, minute=0, second=0, microsecond=0
            )
            cursor = next_day_local.astimezone(cursor.tzinfo)
            continue

        slot_end = cursor + timedelta(minutes=duration_minutes)

        # If the slot would run past closing time, jump to the next day
        slot_end_local = slot_end.astimezone(tz)
        if slot_end_local.hour > work_end_hour or (
            slot_end_local.hour == work_end_hour and slot_end_local.minute > 0
        ):
            next_day_local = (cursor_local + timedelta(days=1)).replace(
                hour=work_start_hour, minute=0, second=0, microsecond=0
            )
            cursor = next_day_local.astimezone(cursor.tzinfo)
            continue

        conflict = next(
            (b for b in busy if intervals_overlap(cursor, slot_end, b[0], b[1])), None
        )

        if conflict:
            cursor = conflict[1]
        else:
            free_slots.append((cursor, slot_end))
            cursor = slot_end

    return free_slots
