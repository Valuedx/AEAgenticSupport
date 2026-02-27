"""
Teams reply helpers.
Replace with adaptive card payloads for richer approval UX.
"""


def make_text_reply(text: str) -> dict:
    return {"type": "message", "text": text}


def make_approval_card(action_summary: str, case_id: str,
                       plan_version: int) -> dict:
    """
    Adaptive Card for approval requests.
    Replace this stub with a real Adaptive Card JSON if your
    Teams integration supports it.
    """
    return {
        "type": "message",
        "text": (
            f"**Approval Required**\n\n"
            f"Action: {action_summary}\n"
            f"Case: {case_id} (v{plan_version})\n\n"
            f"Reply **APPROVE** to proceed or **REJECT** to cancel."
        ),
    }
