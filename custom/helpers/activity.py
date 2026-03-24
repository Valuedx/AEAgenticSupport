"""
Shared Bot Framework activity normalisation helpers.

Used by both the in-process Extension hooks (custom/custom_hooks.py) and the
plan-execute path (custom/functions/python/support_agent.py), as well as the
cognibot proxy (custom_cognibot/custom_hooks.py).
"""
from __future__ import annotations

import re


def extract_user_role(activity) -> str:
    """Return ``"business"`` or ``"technical"`` from a Bot Framework Activity.

    Accepts either an object (attribute access) or a plain dict.  Checks, in
    order: ``user_type``, ``user_role``, then ``channelData.user_role``.
    """
    def _str(v) -> str:
        return str(v or "").strip().lower()

    # Object-style access (activity is a Bot Framework Activity object)
    if not isinstance(activity, dict):
        role = _str(getattr(activity, "user_type", None))
        if not role:
            role = _str(getattr(activity, "user_role", None))
        if not role:
            channel_data = (
                getattr(activity, "channel_data", None)
                or getattr(activity, "channelData", None)
                or {}
            )
            if isinstance(channel_data, dict):
                role = _str(channel_data.get("user_role"))
        return "business" if role == "business" else "technical"

    # Dict-style access (activity is already normalised to a plain dict)
    role = _str(activity.get("user_type") or activity.get("user_role"))
    if not role:
        channel_data = (
            activity.get("channelData") or activity.get("channel_data") or {}
        )
        if isinstance(channel_data, dict):
            role = _str(channel_data.get("user_role"))
    return "business" if role == "business" else "technical"


def classify_approval_intent(text: str) -> str:
    """Return ``"approve"``, ``"reject"``, ``"cancel"``, or ``"other"``.

    Single canonical implementation used by both the hook layer
    (custom/custom_hooks.py) and the plan-execute path
    (custom/functions/python/support_agent.py).

    Word lists:
    - cancel  — explicit withdrawal / stop entirely
    - reject  — disagree with *this* action but continue
    - approve — proceed
    """
    msg = (text or "").strip().lower()
    if not msg:
        return "other"
    if re.search(r"\b(cancel|never mind|abort|forget it)\b", msg):
        return "cancel"
    if re.search(
        r"\b(reject|deny|decline|nope|don'?t do|do not|not now)\b", msg
    ):
        return "reject"
    if re.search(
        r"\b(approve|approved|go ahead|proceed|yes|sure|do it|run it)\b", msg
    ):
        return "approve"
    return "other"
