"""
P0 request_mutate tools — 4 tools for mutating automation requests.
All guarded/privileged operations require reason and support dry_run.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.request_mutate")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ── ae.request.restart_failed ─────────────────────────────────────────

async def request_restart_failed(
    request_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Restart a failed request with the same parameters."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "restart_failed",
            "request_id": request_id,
            "reason": reason,
            "message": f"Would restart failed request {request_id}. No changes made.",
        })

    data = get_ae_client().restart_request(request_id, reason=reason)
    new_id = data.get("requestId") or data.get("id") or data.get("automationRequestId")
    return _safe_json({
        "success": True,
        "action": "restart_failed",
        "request_id": request_id,
        "new_request_id": new_id,
        "status": data.get("status"),
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ── ae.request.restart ────────────────────────────────────────────────

async def request_restart(
    request_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Restart a workflow request (Generic)."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "restart",
            "request_id": request_id,
            "reason": reason,
            "message": f"Would restart request {request_id}. No changes made.",
        })

    data = get_ae_client().restart_request(request_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "restart",
        "request_id": request_id,
        "message": data.get("message", f"Request [{request_id}] has been restarted"),
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ── ae.request.terminate_running ──────────────────────────────────────

async def request_terminate_running(
    request_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Terminate an actively running request. PRIVILEGED operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "terminate_running",
            "request_id": request_id,
            "reason": reason,
            "message": f"Would terminate running request {request_id}. No changes made.",
        })

    data = get_ae_client().terminate_request(request_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "terminate_running",
        "request_id": request_id,
        "status": data.get("status", "Terminated"),
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ── ae.request.resubmit_from_failure_point ────────────────────────────

async def request_resubmit_from_failure_point(
    request_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Resume/resubmit a request from its failure point."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "resubmit_from_failure_point",
            "request_id": request_id,
            "reason": reason,
            "message": f"Would resubmit request {request_id} from failure point. No changes made.",
        })

    data = get_ae_client().resubmit_request(request_id, reason=reason)
    new_id = data.get("requestId") or data.get("id") or data.get("automationRequestId")
    return _safe_json({
        "success": True,
        "action": "resubmit_from_failure_point",
        "request_id": request_id,
        "new_request_id": new_id,
        "status": data.get("status"),
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ── ae.request.add_support_comment ────────────────────────────────────

async def request_add_support_comment(
    request_id: str,
    comment: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Add a support action note/comment to a request."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "add_support_comment",
            "request_id": request_id,
            "comment": comment,
            "message": f"Would add comment to request {request_id}. No changes made.",
        })

    full_comment = comment
    if requested_by or case_id:
        prefix_parts = []
        if case_id:
            prefix_parts.append(f"[Case: {case_id}]")
        if requested_by:
            prefix_parts.append(f"[By: {requested_by}]")
        full_comment = f"{' '.join(prefix_parts)} {comment}"

    data = get_ae_client().add_request_comment(request_id, full_comment)
    return _safe_json({
        "success": True,
        "action": "add_support_comment",
        "request_id": request_id,
        "comment": full_comment,
        "raw": data,
    })


# ── P1 support: cancel_new_or_retry ────────────────────────────────────

async def request_cancel_new_or_retry(
    request_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Cancel a request that has not started (New or Retry)."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "cancel_new_or_retry",
            "request_id": request_id,
            "reason": reason,
            "message": f"Would cancel request {request_id}. No changes made.",
        })
    data = get_ae_client().cancel_request(request_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "cancel_new_or_retry",
        "request_id": request_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ── P1 support: resubmit_from_start ────────────────────────────────────

async def request_resubmit_from_start(
    request_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Resubmit request from the beginning (not from failure point)."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "resubmit_from_start",
            "request_id": request_id,
            "reason": reason,
            "message": f"Would resubmit request {request_id} from start. No changes made.",
        })
    data = get_ae_client().resubmit_request_from_start(request_id, reason=reason)
    new_id = data.get("requestId") or data.get("id") or data.get("automationRequestId")
    return _safe_json({
        "success": True,
        "action": "resubmit_from_start",
        "request_id": request_id,
        "new_request_id": new_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ── P1 support: tag_case_reference ──────────────────────────────────────

async def request_tag_case_reference(
    request_id: str,
    case_id: str,
    requested_by: str = "",
    dry_run: bool = False,
) -> str:
    """Link request to a support case (case reference)."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "tag_case_reference",
            "request_id": request_id,
            "case_id": case_id,
            "message": "Would tag case reference. No changes made.",
        })
    comment = f"[Case: {case_id}]"
    if requested_by:
        comment = f"[By: {requested_by}] [Case: {case_id}]"
    data = get_ae_client().add_request_comment(request_id, comment)
    return _safe_json({
        "success": True,
        "action": "tag_case_reference",
        "request_id": request_id,
        "case_id": case_id,
        "raw": data,
    })


# ── P1 support: raise_manual_handoff ───────────────────────────────────

async def request_raise_manual_handoff(
    request_id: str,
    comment: str = "",
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Mark request for human handling / manual handoff."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "raise_manual_handoff",
            "request_id": request_id,
            "message": "Would mark for manual handoff. No changes made.",
        })
    handoff_note = comment or "Marked for manual handoff."
    if case_id or requested_by:
        handoff_note = f"[Case: {case_id}] [By: {requested_by}] {handoff_note}"
    data = get_ae_client().add_request_comment(request_id, handoff_note)
    return _safe_json({
        "success": True,
        "action": "raise_manual_handoff",
        "request_id": request_id,
        "raw": data,
    })
