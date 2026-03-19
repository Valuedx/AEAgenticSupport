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
            logger.warning(
                "[ticket_cfg] TICKET_API_ENV=prod but TICKET_API_PROD_URL is not set — "
                "falling back to UAT URL"
            )
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
    import tools.ticket_db as ticket_db
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
    fn = "ticket_create"

    # ── Input validation ────────────────────────────────────────────────────
    if not process_name or not process_name.strip():
        logger.error("[%s] Validation failed: process_name is empty", fn)
        return _safe_json({"success": False, "error": "process_name is required."})

    if not description or not description.strip():
        logger.error("[%s] Validation failed: description is empty", fn)
        return _safe_json({"success": False, "error": "description is required."})

    request_type = request_type.strip().title()
    if request_type not in ("Incident", "Request"):
        logger.error(
            "[%s] Validation failed: invalid request_type='%s'. Must be 'Incident' or 'Request'",
            fn, request_type,
        )
        return _safe_json({
            "success": False,
            "error": f"request_type must be 'Incident' or 'Request', got '{request_type}'.",
        })

    # ── Config validation ───────────────────────────────────────────────────
    cfg = _ticket_cfg()

    if not cfg["url"]:
        logger.error(
            "[%s] Config error: TICKET_API_URL is not set in .env (env=%s)",
            fn, cfg["env"],
        )
        return _safe_json({"success": False, "error": "TICKET_API_URL not set in .env."})

    if not cfg["email"]:
        logger.error("[%s] Config error: TICKET_API_EMAIL is not set in .env", fn)
        return _safe_json({"success": False, "error": "TICKET_API_EMAIL not set in .env."})

    if not cfg["password"]:
        logger.error("[%s] Config error: TICKET_API_PASSWORD is not set in .env", fn)
        return _safe_json({"success": False, "error": "TICKET_API_PASSWORD not set in .env."})

    # ── Build request ───────────────────────────────────────────────────────
    form_data = {
        "process_name": process_name.strip(),
        "Description": description.strip(),
        "request_type": request_type,
        "Email": cfg["email"],
        "password": cfg["password"],
    }

    logger.info(
        "[%s] Sending ticket creation request | process='%s' | type='%s' | env='%s' | url='%s'",
        fn, process_name.strip(), request_type, cfg["env"], cfg["url"],
    )

    # ── HTTP call ───────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(verify=False, timeout=cfg["timeout"]) as client:
            response = await client.post(cfg["url"], data=form_data)

        http_status = response.status_code
        raw = (response.text or "").strip()

        # Always log full response so failures are visible in CMD/terminal
        logger.info(
            "[%s] API response received | http_status=%d | env='%s' | raw_response='%s'",
            fn, http_status, cfg["env"], raw,
        )

        # ── Parse response ──────────────────────────────────────────────────
        # API always returns HTTP 200.
        # Success payload : "Ticket raised successfully. Ticket ID: 881"
        # Failure payload : "result not successful"
        ticket_id: str | None = None
        success = False
        raw_lower = raw.lower()

        if "ticket raised successfully" in raw_lower:
            # ── SUCCESS path ────────────────────────────────────────────────
            parts = raw.split("Ticket ID:")
            if len(parts) == 2:
                ticket_id = parts[1].strip().rstrip(".")
                success = True
                logger.info(
                    "[%s] Ticket created successfully | ticket_id='%s' | process='%s' | env='%s'",
                    fn, ticket_id, process_name.strip(), cfg["env"],
                )
            else:
                # API said success but didn't include a Ticket ID — log as warning
                logger.warning(
                    "[%s] API returned success message but 'Ticket ID:' label not found. "
                    "Cannot extract ticket_id. | raw_response='%s' | process='%s' | env='%s'",
                    fn, raw, process_name.strip(), cfg["env"],
                )

        elif "result not successful" in raw_lower:
            # ── KNOWN FAILURE path — e.g. bad credentials, duplicate, unknown process ──
            logger.error(
                "[%s] Ticket creation FAILED — API returned 'result not successful' | "
                "http_status=%d | env='%s' | process='%s' | request_type='%s' | "
                "Likely causes: wrong credentials, unknown process_name, or duplicate ticket. | "
                "raw_response='%s'",
                fn, http_status, cfg["env"],
                process_name.strip(), request_type, raw,
            )

        else:
            # ── UNKNOWN response — log the full body so it is visible in terminal ──
            logger.error(
                "[%s] Ticket creation returned an unrecognised response | "
                "http_status=%d | env='%s' | process='%s' | request_type='%s' | "
                "raw_response='%s'",
                fn, http_status, cfg["env"],
                process_name.strip(), request_type, raw,
            )

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
                if db_saved:
                    logger.info(
                        "[%s] Ticket '%s' persisted to local DB successfully",
                        fn, ticket_id,
                    )
                else:
                    logger.error(
                        "[%s] DB save returned False for ticket_id='%s' — "
                        "ticket was created on HDFC but NOT saved locally",
                        fn, ticket_id,
                    )
            except Exception as db_exc:
                logger.error(
                    "[%s] DB save raised exception for ticket_id='%s': %s",
                    fn, ticket_id, db_exc, exc_info=True,
                )

        return _safe_json({
            "success": success,
            "ticket_id": ticket_id,
            "status": "OPEN" if success else None,
            # Full raw API body — always included so caller/UI can display it
            "api_response": raw,
            "http_status": http_status,
            "process_name": process_name.strip(),
            "request_type": request_type,
            "environment": cfg["env"],
            "user_id": str(user_id or ""),
            "db_saved": db_saved,
            # Human-readable error: use exact API text so the user sees what went wrong
            "error": None if success else (raw or f"API returned HTTP {http_status} with no response body."),
        })

    # ── Network / timeout errors ────────────────────────────────────────────
    except httpx.TimeoutException:
        logger.error(
            "[%s] Request timed out after %ds | url='%s' | env='%s'",
            fn, cfg["timeout"], cfg["url"], cfg["env"],
        )
        return _safe_json({
            "success": False,
            "error": f"Ticket API timed out after {cfg['timeout']}s.",
            "environment": cfg["env"],
        })

    except httpx.ConnectError as exc:
        logger.error(
            "[%s] Connection failed | url='%s' | env='%s' | error=%s",
            fn, cfg["url"], cfg["env"], exc,
        )
        return _safe_json({
            "success": False,
            "error": f"Cannot connect to {cfg['url']}: {exc}",
            "environment": cfg["env"],
        })

    except Exception as exc:
        logger.error(
            "[%s] Unexpected exception | env='%s' | error=%s",
            fn, cfg["env"], exc, exc_info=True,  # full traceback in logs
        )
        return _safe_json({
            "success": False,
            "error": f"Unexpected error: {exc}",
            "environment": cfg["env"],
        })


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
    fn = "ticket_get"

    if not ticket_id or not str(ticket_id).strip():
        logger.error("[%s] Validation failed: ticket_id is empty", fn)
        return _safe_json({"success": False, "error": "ticket_id is required."})

    tid = str(ticket_id).strip()
    logger.info("[%s] Looking up ticket_id='%s'", fn, tid)

    try:
        record = _db().get_ticket(tid)
    except Exception as exc:
        logger.error("[%s] DB lookup failed for ticket_id='%s': %s", fn, tid, exc, exc_info=True)
        return _safe_json({"success": False, "error": f"DB error: {exc}"})

    if not record:
        logger.warning(
            "[%s] ticket_id='%s' not found in local registry",
            fn, tid,
        )
        return _safe_json({
            "success": False,
            "found": False,
            "ticket_id": tid,
            "error": (
                f"Ticket '{tid}' not found in local registry. "
                "It may have been created before lifecycle tracking was enabled."
            ),
        })

    logger.info(
        "[%s] ticket_id='%s' found | status='%s' | process='%s'",
        fn, tid, record.get("status"), record.get("process_name"),
    )
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
    fn = "ticket_close"

    if not ticket_id or not str(ticket_id).strip():
        logger.error("[%s] Validation failed: ticket_id is empty", fn)
        return _safe_json({"success": False, "error": "ticket_id is required."})

    tid = str(ticket_id).strip()
    logger.info("[%s] Attempting to close ticket_id='%s'", fn, tid)

    try:
        record = _db().get_ticket(tid)
    except Exception as exc:
        logger.error("[%s] DB lookup failed for ticket_id='%s': %s", fn, tid, exc, exc_info=True)
        return _safe_json({"success": False, "error": f"DB lookup error: {exc}"})

    if not record:
        logger.error("[%s] ticket_id='%s' not found in local registry — cannot close", fn, tid)
        return _safe_json({
            "success": False,
            "ticket_id": tid,
            "error": f"Ticket '{tid}' not found in local registry.",
        })

    current_status = record.get("status", "")
    if current_status == "CLOSED":
        logger.warning(
            "[%s] ticket_id='%s' is already CLOSED — skipping",
            fn, tid,
        )
        return _safe_json({
            "success": False,
            "ticket_id": tid,
            "status": "CLOSED",
            "error": f"Ticket '{tid}' is already CLOSED. Use reopen to reactivate it.",
        })

    try:
        ok = _db().close_ticket(tid, str(resolution_notes or ""))
    except Exception as exc:
        logger.error(
            "[%s] DB close failed for ticket_id='%s': %s",
            fn, tid, exc, exc_info=True,
        )
        return _safe_json({"success": False, "error": f"DB close error: {exc}"})

    if not ok:
        logger.error(
            "[%s] DB close_ticket returned False for ticket_id='%s'",
            fn, tid,
        )
        return _safe_json({"success": False, "ticket_id": tid, "error": "Failed to close ticket in DB."})

    logger.info(
        "[%s] ticket_id='%s' closed successfully | previous_status='%s' | notes='%s'",
        fn, tid, current_status, resolution_notes,
    )
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
    fn = "ticket_reopen"

    if not ticket_id or not str(ticket_id).strip():
        logger.error("[%s] Validation failed: ticket_id is empty", fn)
        return _safe_json({"success": False, "error": "ticket_id is required."})

    tid = str(ticket_id).strip()
    logger.info("[%s] Attempting to reopen ticket_id='%s'", fn, tid)

    try:
        record = _db().get_ticket(tid)
    except Exception as exc:
        logger.error("[%s] DB lookup failed for ticket_id='%s': %s", fn, tid, exc, exc_info=True)
        return _safe_json({"success": False, "error": f"DB lookup error: {exc}"})

    if not record:
        logger.error("[%s] ticket_id='%s' not found — cannot reopen", fn, tid)
        return _safe_json({"success": False, "ticket_id": tid, "error": f"Ticket '{tid}' not found."})

    current_status = record.get("status", "")
    if current_status not in ("CLOSED",):
        logger.warning(
            "[%s] ticket_id='%s' cannot be reopened — current status is '%s' (must be CLOSED)",
            fn, tid, current_status,
        )
        return _safe_json({
            "success": False,
            "ticket_id": tid,
            "status": current_status,
            "error": f"Ticket '{tid}' is currently {current_status}. Only CLOSED tickets can be reopened.",
        })

    try:
        ok = _db().reopen_ticket(tid)
    except Exception as exc:
        logger.error(
            "[%s] DB reopen failed for ticket_id='%s': %s",
            fn, tid, exc, exc_info=True,
        )
        return _safe_json({"success": False, "error": f"DB reopen error: {exc}"})

    if not ok:
        logger.error(
            "[%s] DB reopen_ticket returned False for ticket_id='%s'",
            fn, tid,
        )
        return _safe_json({"success": False, "ticket_id": tid, "error": "Failed to reopen ticket in DB."})

    logger.info(
        "[%s] ticket_id='%s' reopened successfully | previous_status='%s'",
        fn, tid, current_status,
    )
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
    fn = "ticket_list"

    status = str(status or "").strip().upper()
    process_name = str(process_name or "").strip()
    user_id = str(user_id or "").strip()
    limit = min(int(limit or 20), 100)

    if status and status not in VALID_STATUSES:
        logger.error(
            "[%s] Invalid status filter '%s'. Valid values: %s",
            fn, status, sorted(VALID_STATUSES),
        )
        return _safe_json({
            "success": False,
            "error": f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}",
        })

    logger.info(
        "[%s] Listing tickets | status='%s' | process_name='%s' | user_id='%s' | limit=%d",
        fn, status or "ALL", process_name or "ALL", user_id or "ALL", limit,
    )

    try:
        tickets = _db().list_tickets(
            status=status or None,
            process_name=process_name or None,
            user_id=user_id or None,
            limit=limit,
        )
    except Exception as exc:
        logger.error("[%s] DB list_tickets failed: %s", fn, exc, exc_info=True)
        return _safe_json({"success": False, "error": f"DB list error: {exc}"})

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

    logger.info("[%s] Returning %d tickets", fn, len(summary))
    return _safe_json({
        "success": True,
        "count": len(summary),
        "filters": {
            "status": status or "ALL",
            "process_name": process_name or "ALL",
            "user_id": user_id or "ALL",
        },
        "tickets": summary,
    })