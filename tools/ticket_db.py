"""
ticket_db.py — Local PostgreSQL ledger for HDFC ticket lifecycle management.

Since the HDFC Ticket API only provides a CREATE endpoint, all lifecycle state
(OPEN → IN_PROGRESS → CLOSED → REOPENED) is tracked here in the ticket_registry table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("ops_agent.tools.ticket_db")

# ── Valid statuses ────────────────────────────────────────────────────────────
VALID_STATUSES = {"OPEN", "IN_PROGRESS", "CLOSED", "REOPENED"}


def _get_conn():
    from config.db import get_conn
    return get_conn()


def _row_to_dict(row, cursor) -> dict:
    """Convert a DB row to a dictionary using cursor description."""
    if not row or not cursor.description:
        return {}
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# ═══════════════════════════════════════════════════════════════════════════════
#  WRITE operations
# ═══════════════════════════════════════════════════════════════════════════════

def save_ticket(
    ticket_id: str,
    process_name: str,
    description: str,
    request_type: str = "Request",
    environment: str = "uat",
    conversation_id: str = "",
    workflow_name: str = "",
    user_id: str = "",
) -> bool:
    """
    Insert a newly created ticket into the local registry.
    Called immediately after a successful HDFC API ticket creation.
    Status defaults to OPEN.
    user_id tracks which user raised the ticket for privacy/security audit.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ticket_registry
                        (ticket_id, process_name, description, request_type,
                         status, environment, user_id, conversation_id, workflow_name,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (ticket_id) DO NOTHING
                    """,
                    (
                        str(ticket_id),
                        str(process_name),
                        str(description),
                        str(request_type),
                        str(environment),
                        str(user_id or ""),
                        str(conversation_id or ""),
                        str(workflow_name or ""),
                    ),
                )
            conn.commit()
        logger.info("Ticket %s saved to DB (OPEN) by user=%s", ticket_id, user_id or "unknown")
        return True
    except Exception as exc:
        logger.error("save_ticket failed for %s: %s", ticket_id, exc)
        return False


def get_ticket(ticket_id: str) -> Optional[dict]:
    """
    Fetch a ticket record by ticket_id.
    Returns None if not found.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM ticket_registry WHERE ticket_id = %s",
                    (str(ticket_id),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return _row_to_dict(row, cur)
    except Exception as exc:
        logger.error("get_ticket failed for %s: %s", ticket_id, exc)
        return None


def close_ticket(ticket_id: str, resolution_notes: str = "") -> bool:
    """
    Mark a ticket as CLOSED. Records close timestamp and resolution notes.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ticket_registry
                    SET status = 'CLOSED',
                        closed_at = NOW(),
                        updated_at = NOW(),
                        resolution_notes = %s
                    WHERE ticket_id = %s
                    """,
                    (str(resolution_notes or ""), str(ticket_id)),
                )
                rows_affected = cur.rowcount
            conn.commit()
        if rows_affected == 0:
            logger.warning("close_ticket: no ticket found with ID %s", ticket_id)
            return False
        logger.info("Ticket %s closed in DB", ticket_id)
        return True
    except Exception as exc:
        logger.error("close_ticket failed for %s: %s", ticket_id, exc)
        return False


def reopen_ticket(ticket_id: str) -> bool:
    """
    Reopen a previously closed ticket (status → REOPENED).
    Clears closed_at and resolution_notes.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ticket_registry
                    SET status = 'REOPENED',
                        closed_at = NULL,
                        updated_at = NOW(),
                        resolution_notes = NULL
                    WHERE ticket_id = %s
                    """,
                    (str(ticket_id),),
                )
                rows_affected = cur.rowcount
            conn.commit()
        if rows_affected == 0:
            logger.warning("reopen_ticket: no ticket found with ID %s", ticket_id)
            return False
        logger.info("Ticket %s reopened in DB", ticket_id)
        return True
    except Exception as exc:
        logger.error("reopen_ticket failed for %s: %s", ticket_id, exc)
        return False


def list_tickets(
    status: Optional[str] = None,
    process_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """
    List tickets with optional filtering.
    - status: 'OPEN', 'CLOSED', 'IN_PROGRESS', 'REOPENED', or None for all
    - process_name: partial match (ILIKE)
    - user_id: exact match — only return tickets raised by this user
    - limit: max records to return (default 20)
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                clauses = []
                params: list = []

                if status:
                    clauses.append("status = %s")
                    params.append(str(status).upper())
                if process_name:
                    clauses.append("LOWER(process_name) LIKE %s")
                    params.append(f"%{str(process_name).lower()}%")
                if conversation_id:
                    clauses.append("conversation_id = %s")
                    params.append(str(conversation_id))
                if user_id:
                    clauses.append("user_id = %s")
                    params.append(str(user_id))

                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                params.append(int(limit))

                cur.execute(
                    f"""
                    SELECT * FROM ticket_registry
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()
                return [_row_to_dict(r, cur) for r in rows]
    except Exception as exc:
        logger.error("list_tickets failed: %s", exc)
        return []


def update_ticket_status(ticket_id: str, status: str) -> bool:
    """
    Generic status update (e.g., OPEN → IN_PROGRESS).
    """
    status = str(status).upper()
    if status not in VALID_STATUSES:
        logger.error("Invalid ticket status: %s", status)
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ticket_registry SET status = %s, updated_at = NOW() WHERE ticket_id = %s",
                    (status, str(ticket_id)),
                )
                rows_affected = cur.rowcount
            conn.commit()
        return rows_affected > 0
    except Exception as exc:
        logger.error("update_ticket_status failed for %s: %s", ticket_id, exc)
        return False
