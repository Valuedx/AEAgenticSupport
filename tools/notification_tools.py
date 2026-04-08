"""
Notification tools — HDFC Life ticket creation ONLY.

Other lifecycle operations (get, close, reopen, list) have been
removed per product requirements.  Only the create endpoint is
exposed by the HDFC API.
"""

import logging
import os
import urllib3

from tools.base import ToolDefinition
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.notification")

# Suppress only the InsecureRequestWarning from urllib3 (verify=False)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def create_support_ticket(
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
    import requests as _req

    # ── Input validation ─────────────────────────────────────────────────
    if not process_name or not str(process_name).strip():
        return {"success": False, "error": "process_name is required."}
    if not description or not str(description).strip():
        return {"success": False, "error": "description is required."}

    request_type = str(request_type or "Request").strip().title()
    if request_type not in ("Incident", "Request"):
        return {
            "success": False,
            "error": f"request_type must be 'Incident' or 'Request', got '{request_type}'.",
        }

    # ── Config validation ────────────────────────────────────────────────
    api_env = os.environ.get("TICKET_API_ENV", "uat").strip().lower()
    if api_env == "prod":
        base_url = (
            os.environ.get("TICKET_API_PROD_URL", "").strip()
            or os.environ.get("TICKET_API_URL", "").strip()
        )
    else:
        base_url = os.environ.get("TICKET_API_URL", "").strip()

    email = os.environ.get("TICKET_API_EMAIL", "").strip()
    password = os.environ.get("TICKET_API_PASSWORD", "").strip()
    timeout = int(os.environ.get("TICKET_API_TIMEOUT_SECONDS", "30"))

    if not base_url:
        return {"success": False, "error": "TICKET_API_URL not set in .env."}
    if not email or not password:
        return {
            "success": False,
            "error": "TICKET_API_EMAIL or TICKET_API_PASSWORD not set in .env.",
        }

    # ── Build form data ──────────────────────────────────────────────────
    form_data = {
        "process_name": str(process_name).strip(),
        "Description": str(description).strip(),
        "request_type": request_type,
        "Email": email,
        "password": password,
    }
    logger.info(
        "HDFC Ticket: '%s' (%s) -> %s [%s]",
        process_name.strip(), request_type, base_url, api_env,
    )

    # ── HTTP call ────────────────────────────────────────────────────────
    try:
        resp = _req.post(base_url, data=form_data, timeout=timeout, verify=False)
        raw = (resp.text or "").strip()
        logger.info(
            "HDFC Ticket API response [%d]: %s", resp.status_code, raw[:300],
        )

        # ── Parse response ───────────────────────────────────────────────
        # Success : "Ticket raised successfully. Ticket ID: 881"
        # Failure : "result not successful"  or other text
        success = False
        ticket_id = None

        if "ticket raised successfully" in raw.lower():
            success = True
            parts = raw.split("Ticket ID:")
            if len(parts) == 2:
                ticket_id = parts[1].strip().rstrip(".")

        # ── DB persistence (best-effort, never blocks success) ───────────
        db_saved = False
        if success and ticket_id:
            try:
                from tools.ticket_db import save_ticket
                db_saved = save_ticket(
                    ticket_id=ticket_id,
                    process_name=str(process_name).strip(),
                    description=str(description).strip(),
                    request_type=request_type,
                    environment=api_env,
                    user_id=str(user_id or ""),
                )
            except Exception as db_exc:
                logger.warning("DB save best-effort failed for ticket %s: %s", ticket_id, db_exc)

        return {
            "success": success,
            "ticket_id": ticket_id,
            "message": raw,
            "http_status": resp.status_code,
            "process_name": str(process_name).strip(),
            "request_type": request_type,
            "environment": api_env,
            "user_id": str(user_id or ""),
            "db_saved": db_saved,
            "error": None if success else (raw or f"API returned HTTP {resp.status_code} with no body."),
        }

    except _req.exceptions.Timeout:
        msg = f"Ticket API timed out after {timeout}s."
        logger.error("HDFC Ticket timeout: %s", msg)
        return {"success": False, "error": msg, "environment": api_env}

    except _req.exceptions.ConnectionError as exc:
        msg = f"Cannot connect to {base_url}: {exc}"
        logger.error("HDFC Ticket connection error: %s", msg)
        return {"success": False, "error": msg, "environment": api_env}

    except Exception as exc:
        msg = f"Unexpected error: {exc}"
        logger.exception("Support ticket unexpected error")
        return {"success": False, "error": msg, "environment": api_env}


def create_incident_ticket(
    title: str = "",
    description: str = "",
    priority: str = "",
    assignee_group: str = "",
    user_id: str = "",
) -> dict:
    """
    Backward-compatible alias for ticket creation.

    Older prompts and escalation flows still refer to create_incident_ticket.
    Route them to the HDFC ticket API using Incident semantics.
    """
    process_name = str(title or assignee_group or "Support Incident").strip()
    desc = str(description or "").strip()
    result = create_support_ticket(
        process_name=process_name,
        description=desc,
        request_type="Incident",
        user_id=user_id,
    )
    if isinstance(result, dict):
        result.setdefault("priority", priority)
        result.setdefault("assignee_group", assignee_group)
        result.setdefault("legacy_tool", "create_incident_ticket")
    return result


# ── Register ONLY the ticket creation tool ───────────────────────────────
tool_registry.register(
    ToolDefinition(
        name="create_support_ticket",
        description=(
            "Raise a support ticket in the ticketing system for a failed or "
            "problematic process. Returns the assigned Ticket ID on success. "
            "Use this when the user says 'create ticket', 'raise ticket', 'log incident', "
            "'raise issue', or reports a process failure that needs tracking."
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
    create_support_ticket,
)

tool_registry.register(
    ToolDefinition(
        name="create_incident_ticket",
        description=(
            "Backward-compatible alias for raising an incident ticket in the ticketing system. "
            "Use this for failures that need support tracking. Internally routes to create_support_ticket."
        ),
        category="notification",
        tier="medium_risk",
        parameters={
            "title": {
                "type": "string",
                "description": "Short incident title or process name.",
            },
            "description": {
                "type": "string",
                "description": "Detailed incident description in plain text.",
            },
            "priority": {
                "type": "string",
                "description": "Optional priority label for audit context.",
            },
            "assignee_group": {
                "type": "string",
                "description": "Optional support group or queue label.",
            },
        },
        required_params=["description"],
    ),
    create_incident_ticket,
)
