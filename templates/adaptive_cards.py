"""
Adaptive Cards utility for MS Teams notifications.
Generates JSON schemas for rich, actionable messages.
"""
from typing import Any

def render_approval_card(tool_name: str, args: dict, summary: str) -> dict:
    """Render an Adaptive Card for tool execution approval."""
    card = {
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Action Approval Required",
                "weight": "Bolder",
                "size": "Medium"
            },
            {
                "type": "TextBlock",
                "text": f"The agent is requesting to execute: **{tool_name}**",
                "wrap": True
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Target:", "value": summary},
                    {"title": "Arguments:", "value": str(args)}
                ]
            }
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Approve",
                "data": {"action": "approve", "tool": tool_name}
            },
            {
                "type": "Action.Submit",
                "title": "Reject",
                "style": "destructive",
                "data": {"action": "reject", "tool": tool_name}
            }
        ]
    }
    return card

def render_escalation_card(title: str, description: str, findings: list[dict] | None = None) -> dict:
    """Render an Adaptive Card for incident escalation."""
    body = [
        {
            "type": "TextBlock",
            "text": "🚨 Incident Escalated",
            "weight": "Bolder",
            "size": "Large",
            "color": "Attention"
        },
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "wrap": True
        },
        {
            "type": "TextBlock",
            "text": description,
            "wrap": True
        }
    ]
    
    if findings:
        body.append({
            "type": "TextBlock",
            "text": "Investigation Findings:",
            "weight": "Bolder",
            "spacing": "Medium"
        })
        bullets = ""
        for f in findings[:5]:
            summary = f.get("summary") or f.get("details") or str(f)
            bullets += f"- {summary}\n"
        body.append({
            "type": "TextBlock",
            "text": bullets,
            "wrap": True,
            "isSubtle": True
        })

    card = {
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "View Ticket",
                "url": "https://ae-support.example.com/tickets/current"
            }
        ]
    }
    return card
