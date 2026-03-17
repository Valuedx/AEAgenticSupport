"""
Notification tools â€” email, Teams, and incident ticket creation.
"""

import logging
from datetime import datetime

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry
from tools.agent_debug_tools import analyze_agent_logs

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
    client = get_ae_client()
    payload = {
        "title": title,
        "description": description,
        "priority": priority,
        "assignee_group": assignee_group,
    }

    org = client.default_org_code
    attempts = [
        ("/api/v1/incidents", False),
        (f"/{org}/incidents", True) if org else None,
        ("/incidents", True),
        ("/incidents", False),
    ]

    last_error = ""
    for attempt in attempts:
        if not attempt:
            continue
        path, use_rest_prefix = attempt
        try:
            resp = client.request(
                "POST",
                path,
                payload=payload,
                use_rest_prefix=use_rest_prefix,
            )
            return {
                "success": True,
                "ticket_id": (
                    resp.get("incident_id")
                    or resp.get("ticket_id")
                    or resp.get("id")
                ),
                "title": resp.get("title", title),
                "priority": priority,
                "status": resp.get("status", "OPEN"),
            }
        except Exception as exc:
            last_error = str(exc)
            continue

    # Graceful fallback: do not crash the user flow if external ticket API is unavailable.
    fallback_id = f"LOCAL-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.warning("Incident API unavailable; created local escalation reference %s", fallback_id)
    return {
        "success": True,
        "ticket_id": fallback_id,
        "title": title,
        "priority": priority,
        "status": "PENDING_MANUAL_SYNC",
        "message": (
            "Incident endpoint is unavailable right now. "
            "A local escalation reference has been created for manual follow-up."
        ),
        "warning": last_error,
    }


# â”€â”€ Register notification tools â”€â”€

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

# ===============================================================================
# HDFC Life -- Ticket Generation Tool
# ===============================================================================

def create_hdfc_ticket(
    process_name: str,
    description: str,
    request_type: str = "Request",
    user_id: str = "",
) -> dict:
    """
    Raise a ticket in the HDFC Life ticketing system via the Ticket Generation API.

    Credentials and endpoint are loaded from .env:
      TICKET_API_URL, TICKET_API_EMAIL, TICKET_API_PASSWORD, TICKET_API_ENV
    Returns ticket_id on success.
    """
    import os
    import requests as _req

    if not process_name or not str(process_name).strip():
        return {"success": False, "error": "process_name is required."}
    if not description or not str(description).strip():
        return {"success": False, "error": "description is required."}

    request_type = str(request_type or "Request").strip().title()
    if request_type not in ("Incident", "Request"):
        return {"success": False, "error": f"request_type must be 'Incident' or 'Request', got '{request_type}'."}

    api_env = os.environ.get("TICKET_API_ENV", "uat").strip().lower()
    if api_env == "prod":
        base_url = os.environ.get("TICKET_API_PROD_URL", "").strip() or os.environ.get("TICKET_API_URL", "").strip()
    else:
        base_url = os.environ.get("TICKET_API_URL", "").strip()

    email    = os.environ.get("TICKET_API_EMAIL", "").strip()
    password = os.environ.get("TICKET_API_PASSWORD", "").strip()
    timeout  = int(os.environ.get("TICKET_API_TIMEOUT_SECONDS", "30"))

    if not base_url:
        return {"success": False, "error": "TICKET_API_URL not set in .env."}
    if not email or not password:
        return {"success": False, "error": "TICKET_API_EMAIL or TICKET_API_PASSWORD not set in .env."}

    form_data = {
        "process_name": str(process_name).strip(),
        "Description": str(description).strip(),
        "request_type": request_type,
        "Email": email,
        "password": password,
    }
    logger.info("HDFC Ticket: '%s' (%s) -> %s [%s]", process_name.strip(), request_type, base_url, api_env)

    try:
        resp = _req.post(base_url, data=form_data, timeout=timeout, verify=False)
        raw  = (resp.text or "").strip()
        logger.debug("HDFC Ticket [%d]: %s", resp.status_code, raw[:200])

        success, ticket_id = False, None
        if "ticket raised successfully" in raw.lower():
            success = True
            parts = raw.split("Ticket ID:")
            if len(parts) == 2:
                ticket_id = parts[1].strip().rstrip(".")

        return {"success": success, "ticket_id": ticket_id, "message": raw,
                "http_status": resp.status_code, "process_name": str(process_name).strip(),
                "request_type": request_type, "environment": api_env, "user_id": str(user_id or "")}

    except _req.exceptions.Timeout:
        return {"success": False, "error": f"Ticket API timed out after {timeout}s.", "environment": api_env}
    except _req.exceptions.ConnectionError as exc:
        return {"success": False, "error": f"Cannot connect to {base_url}: {exc}", "environment": api_env}
    except Exception as exc:
        logger.exception("HDFC Ticket unexpected error")
        return {"success": False, "error": f"Unexpected error: {exc}", "environment": api_env}


tool_registry.register(
    ToolDefinition(
        name="create_hdfc_ticket",
        description=(
            "Raise a support ticket in the HDFC Life ticketing system for a failed or "
            "problematic process. Returns the assigned Ticket ID on success."
        ),
        category="notification",
        tier="medium_risk",
        parameters={
            "process_name": {
                "type": "string",
                "description": "Name of the process/workflow to raise the ticket for (e.g., 'Demat Process').",
            },
            "description": {
                "type": "string",
                "description": "Clear description of the error or service request including symptoms and errors.",
            },
            "request_type": {
                "type": "string",
                "description": "'Incident' for failures/outages or 'Request' for service requests. Defaults to 'Request'.",
            },
        },
        required_params=["process_name", "description"],
    ),
    create_hdfc_ticket,
)


# ===============================================================================
# HDFC Ticket Lifecycle -- Ops-Agent Tools (get / close / list / reopen)
# ===============================================================================

def get_hdfc_ticket(ticket_id: str) -> dict:
    """Get status and details of a ticket from the local DB registry."""
    if not ticket_id or not str(ticket_id).strip():
        return {"success": False, "error": "ticket_id is required."}
    from tools.ticket_db import get_ticket
    record = get_ticket(str(ticket_id).strip())
    if not record:
        return {"success": False, "found": False, "ticket_id": ticket_id,
                "error": f"Ticket '{ticket_id}' not found in local registry."}
    return {
        "success": True, "found": True,
        "ticket_id": record.get("ticket_id"),
        "status": record.get("status"),
        "process_name": record.get("process_name"),
        "request_type": record.get("request_type"),
        "description": record.get("description"),
        "environment": record.get("environment"),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "closed_at": str(record.get("closed_at") or ""),
        "resolution_notes": record.get("resolution_notes") or "",
        "workflow_name": record.get("workflow_name") or "",
    }


def close_hdfc_ticket(ticket_id: str, resolution_notes: str = "") -> dict:
    """Mark an HDFC ticket as CLOSED and save resolution notes to DB."""
    if not ticket_id or not str(ticket_id).strip():
        return {"success": False, "error": "ticket_id is required."}
    from tools.ticket_db import get_ticket, close_ticket
    tid = str(ticket_id).strip()
    record = get_ticket(tid)
    if not record:
        return {"success": False, "ticket_id": tid, "error": f"Ticket '{tid}' not found."}
    if record.get("status") == "CLOSED":
        return {"success": False, "ticket_id": tid, "status": "CLOSED",
                "error": f"Ticket '{tid}' is already CLOSED. Use reopen to reactivate."}
    ok = close_ticket(tid, str(resolution_notes or ""))
    if not ok:
        return {"success": False, "ticket_id": tid, "error": "Failed to close ticket in DB."}
    return {"success": True, "ticket_id": tid, "status": "CLOSED",
            "previous_status": record.get("status"),
            "resolution_notes": str(resolution_notes or ""),
            "message": f"Ticket {tid} has been closed successfully.",
            "process_name": record.get("process_name")}


def reopen_hdfc_ticket(ticket_id: str) -> dict:
    """Reopen a CLOSED HDFC ticket (status -> REOPENED)."""
    if not ticket_id or not str(ticket_id).strip():
        return {"success": False, "error": "ticket_id is required."}
    from tools.ticket_db import get_ticket, reopen_ticket
    tid = str(ticket_id).strip()
    record = get_ticket(tid)
    if not record:
        return {"success": False, "ticket_id": tid, "error": f"Ticket '{tid}' not found."}
    if record.get("status") != "CLOSED":
        return {"success": False, "ticket_id": tid, "status": record.get("status"),
                "error": f"Only CLOSED tickets can be reopened. Current status: {record.get('status')}."}
    ok = reopen_ticket(tid)
    if not ok:
        return {"success": False, "ticket_id": tid, "error": "Failed to reopen ticket in DB."}
    return {"success": True, "ticket_id": tid, "status": "REOPENED",
            "previous_status": "CLOSED",
            "message": f"Ticket {tid} has been reopened.",
            "process_name": record.get("process_name")}


def list_hdfc_tickets(
    status: str = "",
    process_name: str = "",
    user_id: str = "",
    limit: int = 20,
) -> dict:
    """List HDFC tickets from the local DB with optional filters."""
    from tools.ticket_db import list_tickets
    status = str(status or "").strip().upper()
    process_name = str(process_name or "").strip()
    limit = min(int(limit or 20), 100)
    valid = {"OPEN", "IN_PROGRESS", "CLOSED", "REOPENED"}
    if status and status not in valid:
        return {"success": False, "error": f"Invalid status '{status}'. Must be: {sorted(valid)}"}
    users = str(user_id or "").strip()
    tickets = list_tickets(status=status or None, process_name=process_name or None, user_id=users or None, limit=limit)
    summary = [{
        "ticket_id": t.get("ticket_id"), "process_name": t.get("process_name"),
        "status": t.get("status"), "request_type": t.get("request_type"),
        "created_at": str(t.get("created_at") or ""), "closed_at": str(t.get("closed_at") or "") or None,
        "resolution_notes": t.get("resolution_notes") or "",
    } for t in tickets]
    return {"success": True, "count": len(summary),
            "filters": {"status": status or "ALL", "process_name": process_name or "ALL"},
            "tickets": summary}


tool_registry.register(
    ToolDefinition(
        name="get_hdfc_ticket",
        description="Get the current status and details of an HDFC Life support ticket from the local registry.",
        category="notification",
        tier="read_only",
        parameters={
            "ticket_id": {"type": "string", "description": "The ticket ID to look up (e.g. '881')."},
        },
        required_params=["ticket_id"],
    ),
    get_hdfc_ticket,
)

tool_registry.register(
    ToolDefinition(
        name="close_hdfc_ticket",
        description=(
            "Close an HDFC Life support ticket. Marks it as CLOSED in the local DB and records resolution notes. "
            "Use when the issue is resolved. User says 'close ticket', 'mark resolved', or 'ticket is fixed'."
        ),
        category="notification",
        tier="medium_risk",
        parameters={
            "ticket_id": {"type": "string", "description": "The ticket ID to close (e.g. '881')."},
            "resolution_notes": {"type": "string", "description": "How the issue was resolved (recommended for audit)."},
        },
        required_params=["ticket_id"],
    ),
    close_hdfc_ticket,
)

tool_registry.register(
    ToolDefinition(
        name="reopen_hdfc_ticket",
        description="Reopen a previously CLOSED HDFC Life ticket. Status changes to REOPENED. Use when an issue recurs.",
        category="notification",
        tier="medium_risk",
        parameters={
            "ticket_id": {"type": "string", "description": "The ticket ID to reopen (e.g. '881')."},
        },
        required_params=["ticket_id"],
    ),
    reopen_hdfc_ticket,
)

tool_registry.register(
    ToolDefinition(
        name="list_hdfc_tickets",
        description="List HDFC Life tickets from the local registry, optionally filtered by status or process name.",
        category="notification",
        tier="read_only",
        parameters={
            "status": {"type": "string", "description": "Filter: 'OPEN', 'CLOSED', 'IN_PROGRESS', 'REOPENED', or empty for all."},
            "process_name": {"type": "string", "description": "Partial process name filter (case-insensitive)."},
            "limit": {"type": "integer", "description": "Max tickets to return (default 20)."},
        },
        required_params=[],
    ),
    list_hdfc_tickets,
)
