"""
MCP tools — HDFC Life Ticket Lifecycle Management

Tools:
  ae.ticket.create  — Raise a ticket via HDFC API + store in local DB (status=OPEN)
  ae.ticket.get     — Get a ticket's current status from local DB
  ae.ticket.close   — Mark a ticket as CLOSED in local DB with resolution notes
  ae.ticket.list    — List tickets filtered by status / process name
  ae.ticket.reopen  — Reopen a CLOSED ticket (status → REOPENED)

All lifecycle state after creation is managed locally in ticket_registry table
since the HDFC API exposes only a create endpoint.

Credentials & endpoints loaded from .env:
  TICKET_API_URL, TICKET_API_PROD_URL, TICKET_API_EMAIL,
  TICKET_API_PASSWORD, TICKET_API_TIMEOUT_SECONDS, TICKET_API_ENV
"""
from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger("ae_mcp.tools.ticket")

VALID_STATUSES = {"OPEN", "IN_PROGRESS", "CLOSED", "REOPENED"}


def _ticket_cfg() -> dict:
    env = os.environ.get("TICKET_API_ENV", "uat").strip().lower()
    if env == "prod":
        base_url = os.environ.get("TICKET_API_PROD_URL", "").strip()
        if not base_url:
            logger.warning("TICKET_API_ENV=prod but TICKET_API_PROD_URL not set — falling back to UAT")
            base_url = os.environ.get("TICKET_API_URL", "").strip()
    else:
        base_url = os.environ.get("TICKET_API_URL", "").strip()
    return {
        "url": base_url,
        "email": os.environ.get("TICKET_API_EMAIL", "").strip(),
        "password": os.environ.get("TICKET_API_PASSWORD", "").strip(),
        "timeout": int(os.environ.get("TICKET_API_TIMEOUT_SECONDS", "30")),
        "env": env,
    }


def _safe_json(obj) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


def _db():
    """Lazy import of ticket_db to avoid circular imports at module load."""
    from tools import ticket_db
    return ticket_db


# ═══════════════════════════════════════════════════════════════════════════════
#  ae.ticket.create
# ═══════════════════════════════════════════════════════════════════════════════

async def ticket_create(
    process_name: str,
    description: str,
    request_type: str = "Request",
    user_id: str = "",
) -> str:
    """
    Create a support ticket in the HDFC Life ticketing system and store it locally.

    Raises a ticket via the HDFC API and immediately saves it to the local
    PostgreSQL ticket_registry table with status OPEN for full lifecycle tracking.

    Args:
        process_name:  Process/workflow name to raise the ticket for (e.g. "Demat Process").
        description:   Error description — include symptoms and error messages.
        request_type:  'Incident' for failures/outages, 'Request' for service requests.
        user_id:       ID or email of the user raising the ticket (for audit / privacy).
    """
    if not process_name or not process_name.strip():
        return _safe_json({"success": False, "error": "process_name is required."})
    if not description or not description.strip():
        return _safe_json({"success": False, "error": "description is required."})

    request_type = request_type.strip().title()
    if request_type not in ("Incident", "Request"):
        return _safe_json({"success": False,
                           "error": f"request_type must be 'Incident' or 'Request', got '{request_type}'."})

    cfg = _ticket_cfg()
    if not cfg["url"]:
        return _safe_json({"success": False, "error": "TICKET_API_URL not set in .env."})
    if not cfg["email"] or not cfg["password"]:
        return _safe_json({"success": False, "error": "TICKET_API_EMAIL or TICKET_API_PASSWORD not set in .env."})

    form_data = {
        "process_name": process_name.strip(),
        "Description": description.strip(),
        "request_type": request_type,
        "Email": cfg["email"],
        "password": cfg["password"],
    }
    logger.info("Creating ticket: process='%s' type='%s' env='%s'", process_name, request_type, cfg["env"])

    try:
        async with httpx.AsyncClient(verify=False, timeout=cfg["timeout"]) as client:
            response = await client.post(cfg["url"], data=form_data)

        raw = (response.text or "").strip()
        http_status = response.status_code
        logger.debug("Ticket API [%d]: %s", http_status, raw[:200])

        ticket_id: str | None = None
        success = False

        if "ticket raised successfully" in raw.lower():
            success = True
            parts = raw.split("Ticket ID:")
            if len(parts) == 2:
                ticket_id = parts[1].strip().rstrip(".")

        # ── Save to DB ──────────────────────────────────────────────────────
        db_saved = False
        if success and ticket_id:
            try:
                db_saved = _db().save_ticket(
                    ticket_id=ticket_id,
                    process_name=process_name.strip(),
                    description=description.strip(),
                    request_type=request_type,
                    environment=cfg["env"],
                    user_id=str(user_id or ""),
                )
            except Exception as db_exc:
                logger.error("DB save failed for ticket %s: %s", ticket_id, db_exc)

        return _safe_json({
            "success": success,
            "ticket_id": ticket_id,
            "status": "OPEN" if success else None,
            "message": raw,
            "http_status": http_status,
            "process_name": process_name.strip(),
            "request_type": request_type,
            "environment": cfg["env"],
            "user_id": str(user_id or ""),
            "db_saved": db_saved,
        })

    except httpx.TimeoutException:
        return _safe_json({"success": False, "error": f"Ticket API timed out after {cfg['timeout']}s.", "environment": cfg["env"]})
    except httpx.ConnectError as exc:
        return _safe_json({"success": False, "error": f"Cannot connect to {cfg['url']}: {exc}", "environment": cfg["env"]})
    except Exception as exc:
        logger.error("ticket_create unexpected error: %s", exc, exc_info=True)
        return _safe_json({"success": False, "error": f"Unexpected error: {exc}", "environment": cfg["env"]})


# ═══════════════════════════════════════════════════════════════════════════════
#  ae.ticket.get
# ═══════════════════════════════════════════════════════════════════════════════

async def ticket_get(ticket_id: str) -> str:
    """
    Get the current status and details of a ticket from the local registry.

    Returns ticket metadata including current status (OPEN/IN_PROGRESS/CLOSED/REOPENED),
    creation timestamp, resolution notes, and process name.

    Args:
        ticket_id: The ticket ID returned when the ticket was created (e.g. "881").
    """
    if not ticket_id or not str(ticket_id).strip():
        return _safe_json({"success": False, "error": "ticket_id is required."})

    try:
        record = _db().get_ticket(str(ticket_id).strip())
    except Exception as exc:
        return _safe_json({"success": False, "error": f"DB error: {exc}"})

    if not record:
        return _safe_json({
            "success": False,
            "found": False,
            "ticket_id": ticket_id,
            "error": f"Ticket '{ticket_id}' not found in local registry. It may have been created before lifecycle tracking was enabled.",
        })

    return _safe_json({
        "success": True,
        "found": True,
        "ticket_id": record.get("ticket_id"),
        "status": record.get("status"),
        "process_name": record.get("process_name"),
        "request_type": record.get("request_type"),
        "description": record.get("description"),
        "environment": record.get("environment"),
        "user_id": record.get("user_id") or "",
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "closed_at": str(record.get("closed_at") or ""),
        "resolution_notes": record.get("resolution_notes") or "",
        "workflow_name": record.get("workflow_name") or "",
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  ae.ticket.close
# ═══════════════════════════════════════════════════════════════════════════════

async def ticket_close(ticket_id: str, resolution_notes: str = "") -> str:
    """
    Close a ticket and record the resolution.

    Marks the ticket as CLOSED in the local registry. Records the closure
    timestamp and optional resolution notes for audit purposes.

    Args:
        ticket_id:        The ticket ID to close (e.g. "881").
        resolution_notes: Description of how the issue was resolved. Recommended for audit trail.
    """
    if not ticket_id or not str(ticket_id).strip():
        return _safe_json({"success": False, "error": "ticket_id is required."})

    tid = str(ticket_id).strip()

    # Verify ticket exists first
    try:
        record = _db().get_ticket(tid)
    except Exception as exc:
        return _safe_json({"success": False, "error": f"DB lookup error: {exc}"})

    if not record:
        return _safe_json({
            "success": False,
            "ticket_id": tid,
            "error": f"Ticket '{tid}' not found in local registry.",
        })

    current_status = record.get("status", "")
    if current_status == "CLOSED":
        return _safe_json({
            "success": False,
            "ticket_id": tid,
            "status": "CLOSED",
            "error": f"Ticket '{tid}' is already CLOSED. Use reopen to reactivate it.",
        })

    try:
        ok = _db().close_ticket(tid, str(resolution_notes or ""))
    except Exception as exc:
        return _safe_json({"success": False, "error": f"DB close error: {exc}"})

    if not ok:
        return _safe_json({"success": False, "ticket_id": tid, "error": "Failed to close ticket in DB."})

    return _safe_json({
        "success": True,
        "ticket_id": tid,
        "status": "CLOSED",
        "previous_status": current_status,
        "resolution_notes": str(resolution_notes or ""),
        "message": f"Ticket {tid} has been closed successfully.",
        "process_name": record.get("process_name"),
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  ae.ticket.reopen
# ═══════════════════════════════════════════════════════════════════════════════

async def ticket_reopen(ticket_id: str) -> str:
    """
    Reopen a previously closed ticket.

    Sets ticket status back to REOPENED and clears the closure timestamp
    so the issue can be tracked again.

    Args:
        ticket_id: The ticket ID to reopen (e.g. "881").
    """
    if not ticket_id or not str(ticket_id).strip():
        return _safe_json({"success": False, "error": "ticket_id is required."})

    tid = str(ticket_id).strip()

    try:
        record = _db().get_ticket(tid)
    except Exception as exc:
        return _safe_json({"success": False, "error": f"DB lookup error: {exc}"})

    if not record:
        return _safe_json({"success": False, "ticket_id": tid, "error": f"Ticket '{tid}' not found."})

    current_status = record.get("status", "")
    if current_status not in ("CLOSED",):
        return _safe_json({
            "success": False,
            "ticket_id": tid,
            "status": current_status,
            "error": f"Ticket '{tid}' is currently {current_status}. Only CLOSED tickets can be reopened.",
        })

    try:
        ok = _db().reopen_ticket(tid)
    except Exception as exc:
        return _safe_json({"success": False, "error": f"DB reopen error: {exc}"})

    if not ok:
        return _safe_json({"success": False, "ticket_id": tid, "error": "Failed to reopen ticket in DB."})

    return _safe_json({
        "success": True,
        "ticket_id": tid,
        "status": "REOPENED",
        "previous_status": current_status,
        "message": f"Ticket {tid} has been reopened. Status is now REOPENED.",
        "process_name": record.get("process_name"),
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  ae.ticket.list
# ═══════════════════════════════════════════════════════════════════════════════

async def ticket_list(
    status: str = "",
    process_name: str = "",
    user_id: str = "",
    limit: int = 20,
) -> str:
    """
    List tickets from the local registry with optional filters.

    Returns a list of tickets sorted by creation date (newest first).

    Args:
        status:       Filter by status: 'OPEN', 'CLOSED', 'IN_PROGRESS', 'REOPENED', or empty for all.
        process_name: Filter by process name (partial match, case-insensitive).
        user_id:      Filter by the user who raised the ticket (exact match).
        limit:        Maximum number of tickets to return (default 20, max 100).
    """
    status = str(status or "").strip().upper()
    process_name = str(process_name or "").strip()
    user_id = str(user_id or "").strip()
    limit = min(int(limit or 20), 100)

    if status and status not in VALID_STATUSES:
        return _safe_json({
            "success": False,
            "error": f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}",
        })

    try:
        tickets = _db().list_tickets(
            status=status or None,
            process_name=process_name or None,
            user_id=user_id or None,
            limit=limit,
        )
    except Exception as exc:
        return _safe_json({"success": False, "error": f"DB list error: {exc}"})

    # Summarise for display
    summary = []
    for t in tickets:
        summary.append({
            "ticket_id": t.get("ticket_id"),
            "process_name": t.get("process_name"),
            "status": t.get("status"),
            "request_type": t.get("request_type"),
            "user_id": t.get("user_id") or "",
            "created_at": str(t.get("created_at") or ""),
            "closed_at": str(t.get("closed_at") or "") or None,
            "resolution_notes": t.get("resolution_notes") or "",
        })

    return _safe_json({
        "success": True,
        "count": len(summary),
        "filters": {"status": status or "ALL", "process_name": process_name or "ALL", "user_id": user_id or "ALL"},
        "tickets": summary,
    })
