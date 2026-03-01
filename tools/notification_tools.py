"""
Notification tools — email, Teams, and incident ticket creation.
"""

import logging

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.notification")


def send_notification(channel: str, recipients: list[str],
                      subject: str, message: str) -> dict:
    resp = get_ae_client().post(
        "/api/v1/notifications/send",
        payload={
            "channel": channel,
            "recipients": recipients,
            "subject": subject,
            "message": message,
        },
    )
    return {
        "success": True,
        "channel": resp.get("channel", channel),
        "recipients_count": len(recipients),
        "status": resp.get("status"),
        "notification_id": resp.get("notification_id"),
    }


def create_incident_ticket(title: str, description: str,
                           priority: str = "P3",
                           assignee_group: str = "") -> dict:
    resp = get_ae_client().post(
        "/api/v1/incidents",
        payload={
            "title": title,
            "description": description,
            "priority": priority,
            "assignee_group": assignee_group,
        },
    )
    return {
        "success": True,
        "ticket_id": resp.get("incident_id"),
        "title": resp.get("title"),
        "priority": priority,
        "status": resp.get("status"),
    }


# ── Register notification tools ──

tool_registry.register(
    ToolDefinition(
        name="send_notification",
        description=(
            "Send an alert notification to team members via email "
            "or Microsoft Teams."
        ),
        category="notification",
        tier="medium_risk",
        parameters={
            "channel": {
                "type": "string",
                "description": "Notification channel: 'email' or 'teams'",
            },
            "recipients": {
                "type": "array",
                "description": "List of recipient IDs or email addresses",
            },
            "subject": {
                "type": "string",
                "description": "Notification subject",
            },
            "message": {
                "type": "string",
                "description": "Notification body",
            },
        },
        required_params=["channel", "recipients", "subject", "message"],
    ),
    send_notification,
)

tool_registry.register(
    ToolDefinition(
        name="create_incident_ticket",
        description=(
            "Create an ITSM incident ticket for tracking and escalation."
        ),
        category="notification",
        tier="medium_risk",
        parameters={
            "title": {
                "type": "string",
                "description": "Ticket title",
            },
            "description": {
                "type": "string",
                "description": "Detailed description",
            },
            "priority": {
                "type": "string",
                "description": "Priority: P1, P2, P3, P4",
            },
            "assignee_group": {
                "type": "string",
                "description": "Team to assign to",
            },
        },
        required_params=["title", "description"],
    ),
    create_incident_ticket,
)
