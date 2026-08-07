"""
Combines Google Calendar free/busy data with time_utils to answer the
question: "is this slot free, and if not, what are 3 good alternatives?"
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services import google_calendar_service as calendar_service
from utils import time_utils


def check_conflict_and_suggest(
    access_token: str,
    date_str: str,
    time_str: str,
    duration_minutes: int,
    timezone_name: str,
    search_days: int = 5,
    max_alternatives: int = 3,
) -> dict:
    """
    Returns:
    {
      "conflict": bool,
      "busy_periods": [...],
      "alternatives": [{"date", "time", "timezone", "start_datetime", "end_datetime"}],
      "requested": {"start_datetime", "end_datetime", "date", "time", "timezone"}
    }
    """
    start_dt_str = time_utils.to_rfc3339(date_str, time_str, timezone_name)
    end_dt_str = time_utils.add_minutes(start_dt_str, duration_minutes)

    start_dt = time_utils.parse_rfc3339(start_dt_str)
    end_dt = time_utils.parse_rfc3339(end_dt_str)

    search_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    search_end = search_start + timedelta(days=search_days)

    busy_periods = calendar_service.get_freebusy(
        access_token,
        search_start.isoformat(),
        search_end.isoformat(),
    )

    conflict = any(
        time_utils.intervals_overlap(
            start_dt, end_dt, time_utils.parse_rfc3339(b["start"]), time_utils.parse_rfc3339(b["end"])
        )
        for b in busy_periods
    )

    alternatives = []
    if conflict:
        free_slots = time_utils.find_free_slots(
            busy_periods,
            duration_minutes,
            timezone_name,
            start_dt,
            search_end,
            max_slots=max_alternatives,
        )
        tz = ZoneInfo(timezone_name)
        for slot_start, slot_end in free_slots:
            local_start = slot_start.astimezone(tz)
            alternatives.append(
                {
                    "date": local_start.strftime("%Y-%m-%d"),
                    "time": local_start.strftime("%H:%M"),
                    "timezone": timezone_name,
                    "start_datetime": slot_start.isoformat(),
                    "end_datetime": slot_end.isoformat(),
                }
            )

    return {
        "conflict": conflict,
        "busy_periods": busy_periods,
        "alternatives": alternatives,
        "requested": {
            "start_datetime": start_dt_str,
            "end_datetime": end_dt_str,
            "date": date_str,
            "time": time_str,
            "timezone": timezone_name,
        },
    }


def find_open_slots(
    access_token: str,
    date_str: str,
    duration_minutes: int,
    timezone_name: str,
    search_days: int = 5,
    max_slots: int = 5,
) -> dict:
    """
    Answers "what's free?" starting from date_str, with no specific requested
    time to check against. Used by the AI chat assistant when the person asks
    something like "show me my free slots tomorrow" or "when am I free this
    week" rather than naming an exact time to validate.

    Returns:
    {
      "search_start_date": "YYYY-MM-DD",
      "busy_periods": [...],
      "free_slots": [{"date", "time", "timezone", "start_datetime", "end_datetime"}]
    }
    """
    tz = ZoneInfo(timezone_name)
    search_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=tz
    )
    now_local = datetime.now(tz)
    if search_start < now_local.replace(hour=0, minute=0, second=0, microsecond=0):
        search_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    search_end = search_start + timedelta(days=search_days)

    busy_periods = calendar_service.get_freebusy(
        access_token,
        search_start.isoformat(),
        search_end.isoformat(),
    )

    free_slots = []
    raw_slots = time_utils.find_free_slots(
        busy_periods,
        duration_minutes,
        timezone_name,
        max(search_start, now_local),
        search_end,
        max_slots=max_slots,
    )
    for slot_start, slot_end in raw_slots:
        local_start = slot_start.astimezone(tz)
        free_slots.append(
            {
                "date": local_start.strftime("%Y-%m-%d"),
                "time": local_start.strftime("%H:%M"),
                "timezone": timezone_name,
                "start_datetime": slot_start.isoformat(),
                "end_datetime": slot_end.isoformat(),
            }
        )

    return {
        "search_start_date": date_str,
        "busy_periods": busy_periods,
        "free_slots": free_slots,
    }


def count_meetings_on_date(access_token: str, date_str: str, timezone_name: str) -> int:
    """
    Returns the number of timed calendar events on a given date.
    Used by the necessity checker to detect overloaded days.
    """
    from services import google_calendar_service as calendar_service
    tz = ZoneInfo(timezone_name)
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=tz
    )
    day_end = day_start + timedelta(days=1)
    events = calendar_service.list_events(
        access_token,
        day_start.isoformat(),
        day_end.isoformat(),
        max_results=50,
    )
    # Count only timed events, not all-day
    return sum(1 for e in events if "dateTime" in e.get("start", {}))
