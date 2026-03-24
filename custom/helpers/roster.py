"""
On-shift tech user roster.
Determines which tech users are available to approve risky actions.
Replace TECH_ROSTER with your real roster (DB table or config).
"""
from __future__ import annotations

from datetime import datetime, time, timezone as dt_timezone
from typing import Dict, List

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


TECH_ROSTER = [
    {
        "teams_user_id": "TECH1_TEAMS_ID",
        "shift": {"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"},
        "skills": ["AE_PLATFORM"],
    },
    # Add more tech users here
]


def _now_in_tz(tz_name: str) -> datetime:
    """Return the current wall-clock time in *tz_name* (e.g. ``"Asia/Kolkata"``)."""
    return datetime.now(ZoneInfo(tz_name))


def is_on_shift(now_utc_or_aware: datetime, shift: Dict) -> bool:
    """Return True if *now_utc_or_aware* falls within the shift window.

    *shift* must contain ``"start"``, ``"end"`` (``"HH:MM"``), and
    ``"timezone"`` (IANA name, e.g. ``"Asia/Kolkata"``).  The datetime is
    converted to the shift's local timezone before comparison so that rosters
    configured in IST are not evaluated against server-local (often UTC) time.
    """
    tz = ZoneInfo(shift.get("timezone", "UTC"))
    if now_utc_or_aware.tzinfo is None:
        # Treat naive datetime as UTC for conversion
        now_local = now_utc_or_aware.replace(tzinfo=dt_timezone.utc).astimezone(tz)
    else:
        now_local = now_utc_or_aware.astimezone(tz)

    s_h, s_m = map(int, shift["start"].split(":"))
    e_h, e_m = map(int, shift["end"].split(":"))
    start = time(s_h, s_m)
    end = time(e_h, e_m)
    t = now_local.time()
    if start <= end:
        return start <= t <= end
    # Overnight shift (e.g. 22:00 – 06:00)
    return t >= start or t <= end


def pick_onshift_techs(roster: List[Dict] = None,
                       now_local: datetime = None) -> List[str]:
    """Return Teams user IDs for techs currently on shift.

    *now_local* is accepted for backwards compatibility but is treated as UTC
    when naive (no tzinfo).  Pass a timezone-aware datetime or omit to use the
    real current UTC time.
    """
    roster = roster or TECH_ROSTER
    now = now_local if now_local is not None else datetime.now(dt_timezone.utc)
    onshift = []
    for r in roster:
        if is_on_shift(now, r["shift"]):
            onshift.append(r["teams_user_id"])
    return onshift[:5]
