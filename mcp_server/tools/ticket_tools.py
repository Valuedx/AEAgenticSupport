"""
MCP tool — HDFC Life Ticket Creation (ae.ticket.create)

Raises a ticket via the HDFC Ticket API and returns the Ticket ID.
Credentials loaded from .env: TICKET_API_URL, TICKET_API_EMAIL, TICKET_API_PASSWORD
"""
from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger("ae_mcp.tools.ticket")


def _safe_json(obj) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


async def ticket_create(
    process_name: str,
    description: str,
    request_type: str = "Request",
) -> str:
    r"""
    Create a support ticket in the HDFC Life ticketing system.

    CRITICAL: Use plain text only. Avoid double quotes ("), backslashes (\), 
    or structural delimiters (like JSON or code blocks) in the description 
    as they trigger security filters and break the API payload.

    Args:
        process_name: Process/workflow name (plain text, no quotes).
        description:  Detailed error description (plain text, no quotes).
        request_type: 'Incident' or 'Request' (default: 'Request').
    """
    if not process_name or not process_name.strip():
        return _safe_json({"success": False, "error": "process_name is required."})
    if not description or not description.strip():
        return _safe_json({"success": False, "error": "description is required."})

    request_type = (request_type or "Request").strip().title()
    if request_type not in ("Incident", "Request"):
        return _safe_json({"success": False, "error": f"request_type must be 'Incident' or 'Request', got '{request_type}'."})

    # Load config from .env
    env = os.environ.get("TICKET_API_ENV", "uat").strip().lower()
    url = os.environ.get("TICKET_API_PROD_URL" if env == "prod" else "TICKET_API_URL", "").strip()
    if not url:
        url = os.environ.get("TICKET_API_URL", "").strip()
    email = os.environ.get("TICKET_API_EMAIL", "").strip()
    password = os.environ.get("TICKET_API_PASSWORD", "").strip()
    timeout = int(os.environ.get("TICKET_API_TIMEOUT_SECONDS", "30"))

    if not url:
        return _safe_json({"success": False, "error": "TICKET_API_URL not set in .env."})
    if not email or not password:
        return _safe_json({"success": False, "error": "TICKET_API_EMAIL or TICKET_API_PASSWORD not set."})

    # Ultra-aggressive sanitization to avoid "malicious code" or "breaking payload" rejection by WAF
    # Strip ALL special characters except alphanumeric, spaces, and hyphens.
    import re
    def _ultra_clean(val: str) -> str:
        if not val: return ""
        # First replace common delimiters with spaces to preserve word boundaries
        val = val.replace("_", " ").replace(":", " ").replace("/", " ").replace("\\", " ").replace("-", " ")
        # Then strip everything that isn't alphanumeric or space
        cleaned = re.sub(r'[^a-zA-Z0-9 ]', '', val)
        return " ".join(cleaned.split()) # Normalize spaces

    form_data = {
        "process_name": _ultra_clean(process_name),
        "Description": _ultra_clean(description),
        "request_type": _ultra_clean(request_type),
        "Email": email,
        "password": password,
    }

    # Debug log (masking password)
    safe_payload = {k: (v if k != "password" else "****") for k, v in form_data.items()}
    logger.info("Ticket API Payload (Sanitized): %s", json.dumps(safe_payload))
    logger.info("Creating ticket: process='%s' type='%s' env='%s'", process_name.strip(), request_type, env)

    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            resp = await client.post(url, data=form_data)

        raw = (resp.text or "").strip()
        logger.info("Ticket API [%d]: %s", resp.status_code, raw)

        # Parse: "Ticket raised successfully. Ticket ID: 886"
        if "ticket raised successfully" in raw.lower():
            ticket_id = None
            parts = raw.split("Ticket ID:")
            if len(parts) == 2:
                ticket_id = parts[1].strip().rstrip(".")
            return _safe_json({
                "success": True,
                "ticket_id": ticket_id,
                "message": raw,
                "process_name": process_name.strip(),
                "request_type": request_type,
            })
        else:
            return _safe_json({
                "success": False,
                "error": raw or f"API returned HTTP {resp.status_code}",
                "process_name": process_name.strip(),
            })

    except httpx.TimeoutException:
        return _safe_json({"success": False, "error": f"Ticket API timed out after {timeout}s."})
    except httpx.ConnectError as e:
        return _safe_json({"success": False, "error": f"Cannot connect to {url}: {e}"})
    except Exception as e:
        logger.exception("Ticket creation error")
        return _safe_json({"success": False, "error": f"Unexpected error: {e}"})