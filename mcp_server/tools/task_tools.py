"""
P0 task_read tools — 2 tools.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.task")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


async def task_get_request_context(task_id: str) -> str:
    """Map a task to its parent request and workflow."""
    data = get_ae_client().get_task(task_id)
    request_id = data.get("requestId") or data.get("automationRequestId")

    request_info = None
    if request_id:
        try:
            req = get_ae_client().get_request(str(request_id))
            request_info = {
                "request_id": request_id,
                "workflow_name": req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name"),
                "status": req.get("status"),
                "submitted_by": req.get("userId"),
                "created": req.get("createdDate"),
            }
        except Exception:
            pass

    return _safe_json({
        "task_id": task_id,
        "task_status": data.get("status"),
        "assignee": data.get("assignee") or data.get("assignedTo"),
        "group": data.get("group") or data.get("assignedGroup"),
        "request_id": request_id,
        "request_context": request_info,
    })


async def task_list_blocking_requests(
    workflow: str = "",
    limit: int = 50,
) -> str:
    """List requests that are blocked by pending tasks (Awaiting Input)."""
    filters: dict[str, Any] = {"status": "Awaiting Input"}
    if workflow:
        filters["workflowName"] = workflow

    requests = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in requests[:limit]:
        req_id = r.get("id") or r.get("automationRequestId")
        task_info = r.get("taskInfo") or r.get("manualTask") or {}
        items.append({
            "request_id": req_id,
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "status": r.get("status"),
            "pending_task_id": task_info.get("taskId") or task_info.get("id") or r.get("pendingTaskId"),
            "assignee": task_info.get("assignee") or task_info.get("assignedTo"),
            "created": r.get("createdDate"),
        })
    return _safe_json({"blocked_requests": items, "count": len(items)})


# ═══════════════════════════════════════════════════════════════════════
#  P1 support: search_pending, get_assignees, get_overdue, cancel_admin, reassign, explain_awaiting_input
# ═══════════════════════════════════════════════════════════════════════

async def task_search_pending(workflow: str = "", limit: int = 50) -> str:
    """Search pending/awaiting-approval tasks."""
    filters: dict[str, Any] = {"status": "Pending"}
    if workflow:
        filters["workflowName"] = workflow
    tasks = get_ae_client().get_tasks(filters)
    items = [{"task_id": t.get("id") or t.get("taskId"), "request_id": t.get("requestId"), "assignee": t.get("assignee") or t.get("assignedTo"), "created": t.get("createdDate")} for t in (tasks or [])[:limit]]
    return _safe_json({"pending_tasks": items, "count": len(items)})


async def task_get_assignees(task_id: str) -> str:
    """Show assignees (user/group) for a task."""
    data = get_ae_client().get_task(task_id)
    assignee = data.get("assignee") or data.get("assignedTo") or data.get("assignedUser")
    group = data.get("group") or data.get("assignedGroup")
    return _safe_json({"task_id": task_id, "assignee": assignee, "group": group})


async def task_get_overdue(limit: int = 50) -> str:
    """List overdue tasks."""
    tasks = get_ae_client().get_tasks({"status": "Pending"})
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    overdue = []
    for t in tasks or []:
        due = t.get("deadline") or t.get("dueDate")
        if due:
            try:
                ts = due / 1000 if isinstance(due, (int, float)) and due > 1e12 else due
                if isinstance(ts, (int, float)) and ts < now.timestamp():
                    overdue.append({"task_id": t.get("id") or t.get("taskId"), "assignee": t.get("assignee"), "deadline": due})
            except Exception:
                pass
        if len(overdue) >= limit:
            break
    return _safe_json({"overdue_tasks": overdue, "count": len(overdue)})


async def task_cancel_admin(
    task_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Cancel a task as admin. Guarded operation."""
    if dry_run:
        return _safe_json({"dry_run": True, "action": "cancel_task", "task_id": task_id, "reason": reason, "message": f"Would cancel task {task_id}. No changes made."})
    data = get_ae_client().cancel_task(task_id, reason=reason)
    return _safe_json({"success": True, "action": "cancel_task", "task_id": task_id, "reason": reason, "requested_by": requested_by, "case_id": case_id, "raw": data})


async def task_reassign(
    task_id: str,
    target_user_or_group: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Reassign a task to another user or group. Guarded operation."""
    if dry_run:
        return _safe_json({"dry_run": True, "action": "reassign_task", "task_id": task_id, "target": target_user_or_group, "reason": reason, "message": "Would reassign. No changes made."})
    data = get_ae_client().reassign_task(task_id, target_user_or_group, reason=reason)
    return _safe_json({"success": True, "action": "reassign_task", "task_id": task_id, "target": target_user_or_group, "reason": reason, "requested_by": requested_by, "case_id": case_id, "raw": data})


async def task_explain_awaiting_input(request_id: str) -> str:
    """Explain why a request is blocked (awaiting input/approval)."""
    client = get_ae_client()
    req = client.get_request(request_id)
    task_info = req.get("taskInfo") or req.get("manualTask") or {}
    task_id = task_info.get("taskId") or req.get("pendingTaskId")
    blocking = []
    if task_id:
        try:
            t = client.get_task(task_id)
            blocking.append({"task_id": task_id, "assignee": t.get("assignee") or t.get("assignedTo"), "group": t.get("group"), "status": t.get("status")})
        except Exception:
            pass
    return _safe_json({
        "request_id": request_id,
        "status": req.get("status"),
        "explanation": "Request is awaiting human input or approval." if blocking else "No blocking task found.",
        "blocking_tasks": blocking,
    })
