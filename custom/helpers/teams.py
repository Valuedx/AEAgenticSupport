"""
Teams reply helpers.
"""
from __future__ import annotations

from typing import Optional, List


def make_text_reply(text: str) -> dict:
    return {"type": "message", "text": text}


def make_approval_card(case_id: str, action_summary: str,
                       reviewers: Optional[List[str]] = None,
                       plan_version: Optional[int] = None) -> dict:
    """
    Returns a Teams message with an Adaptive Card attachment for approvals.

    Clicking Approve/Reject sends an ``activity.value = {"action": "approve"|"reject",
    "case_id": <case_id>}`` payload which ``_extract_text`` in custom_hooks.py maps
    back to the expected approval word.

    Falls back gracefully: if Teams cannot render Adaptive Cards the ``text``
    field is still a plain-text prompt.
    """
    reviewer_text = (
        f"Authorized reviewers: {', '.join(reviewers)}"
        if reviewers else "Any authorized team member can respond"
    )
    version_text = f" (v{plan_version})" if plan_version else ""
    plain_text = (
        f"Approval Required — {action_summary} "
        f"[Case: {case_id}{version_text}]. "
        f"{reviewer_text}. "
        f"Reply APPROVE to proceed or REJECT to cancel."
    )

    card = {
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Action Approval Required",
                "weight": "Bolder",
                "size": "Medium",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Action:", "value": action_summary},
                    {"title": "Case:", "value": f"{case_id}{version_text}"},
                    {"title": "Reviewers:", "value": reviewer_text},
                ],
            },
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Approve",
                "data": {"action": "approve", "case_id": case_id},
            },
            {
                "type": "Action.Submit",
                "title": "Reject",
                "style": "destructive",
                "data": {"action": "reject", "case_id": case_id},
            },
        ],
    }

    return {
        "type": "message",
        "text": plain_text,
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }
