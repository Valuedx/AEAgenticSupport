"""
Teams reply helpers.
Replace with adaptive card payloads for richer approval UX.
"""
from __future__ import annotations

from typing import Optional, List


def make_text_reply(text: str) -> dict:
    return {"type": "message", "text": text}


def make_approval_card(case_id: str, action_summary: str,
                       reviewers: Optional[List[str]] = None,
                       plan_version: Optional[int] = None) -> dict:
    """
    Adaptive Card for approval requests.
    Replace this stub with a real Adaptive Card JSON if your
    Teams integration supports it.
    """
    reviewer_text = (
        f"Authorized reviewers: {', '.join(reviewers)}"
        if reviewers else "Any authorized team member can respond"
    )
    version_text = f" (v{plan_version})" if plan_version else ""
    return {
        "type": "message",
        "text": (
            f"**Approval Required**\n\n"
            f"Action: {action_summary}\n"
            f"Case: {case_id}{version_text}\n"
            f"{reviewer_text}\n\n"
            f"Reply **APPROVE** to proceed or **REJECT** to cancel."
        ),
    }
