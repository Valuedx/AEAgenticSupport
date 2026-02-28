"""
On-shift tech user roster.
Determines which tech users are available to approve risky actions.
Replace TECH_ROSTER with your real roster (DB table or config).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Dict, List


TECH_ROSTER = [
    {
        "teams_user_id": "TECH1_TEAMS_ID",
        "shift": {"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"},
        "skills": ["AE_PLATFORM"],
    },
    # Add more tech users here
]


def is_on_shift(now_local: datetime, shift: Dict) -> bool:
    s_h, s_m = map(int, shift["start"].split(":"))
    e_h, e_m = map(int, shift["end"].split(":"))
    start = time(s_h, s_m)
    end = time(e_h, e_m)
    t = now_local.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def pick_onshift_techs(roster: List[Dict] = None,
                       now_local: datetime = None) -> List[str]:
    roster = roster or TECH_ROSTER
    now_local = now_local or datetime.now()
    onshift = []
    for r in roster:
        if is_on_shift(now_local, r["shift"]):
            onshift.append(r["teams_user_id"])
    return onshift[:5]
