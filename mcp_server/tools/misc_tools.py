"""
P0 tools for: user_read (1), permission_read (2), platform_read (2), result_read (1) — 6 total.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.misc")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ═══════════════════════════════════════════════════════════════════════
#  user_read
# ═══════════════════════════════════════════════════════════════════════

async def user_get_accessible_workflows(user_id: str) -> str:
    """Get workflows accessible to a user."""
    results = get_ae_client().get_user_workflows(user_id)
    items = []
    for w in results:
        items.append({
            "workflow_id": w.get("workflowId") or w.get("id"),
            "workflow_name": w.get("workflowName") or w.get("name"),
            "description": w.get("description"),
            "active": w.get("active"),
        })
    return _safe_json({"user_id": user_id, "accessible_workflows": items, "count": len(items)})


# ═══════════════════════════════════════════════════════════════════════
#  permission_read
# ═══════════════════════════════════════════════════════════════════════

async def permission_get_workflow_permissions(workflow_id: str) -> str:
    """Get the permission map for a workflow."""
    data = get_ae_client().get_workflow_permissions(workflow_id)
    return _safe_json({"workflow_id": workflow_id, "permissions": data})


async def permission_explain_user_access_issue(
    user_id: str,
    workflow_id: str,
) -> str:
    """One-shot diagnosis of why a user cannot see or run a workflow."""
    client = get_ae_client()
    findings: list[str] = []

    user_workflows = []
    try:
        user_workflows = client.get_user_workflows(user_id)
    except Exception as e:
        findings.append(f"Could not fetch user workflows: {e}")

    wf_found = any(
        (w.get("workflowId") or w.get("id") or "") == workflow_id
        or (w.get("workflowName") or w.get("name") or "") == workflow_id
        for w in user_workflows
    )

    if wf_found:
        findings.append("Workflow IS in the user's accessible list — user should be able to see it.")
    else:
        findings.append("Workflow is NOT in the user's accessible list — access is restricted.")

    wf_active = True
    try:
        wf = client.get_workflow(workflow_id)
        if not wf.get("active", True):
            wf_active = False
            findings.append("Workflow is INACTIVE — disabled workflows may not appear for users.")
    except Exception as e:
        findings.append(f"Could not fetch workflow details: {e}")

    try:
        perms = client.get_workflow_permissions(workflow_id)
        if isinstance(perms, dict):
            users = perms.get("users") or perms.get("allowedUsers") or []
            groups = perms.get("groups") or perms.get("allowedGroups") or []
            if users or groups:
                user_in_acl = user_id in [str(u) for u in users]
                if user_in_acl:
                    findings.append("User IS in the workflow's user ACL.")
                else:
                    findings.append(
                        "User is NOT in the workflow's user ACL. "
                        f"Allowed users: {users[:5]}. Groups: {groups[:5]}."
                    )
            else:
                findings.append("No explicit user/group restrictions on the workflow.")
    except Exception:
        pass

    if not findings:
        findings.append("No issues detected. User should have access.")

    return _safe_json({
        "user_id": user_id,
        "workflow_id": workflow_id,
        "workflow_active": wf_active,
        "user_has_access": wf_found,
        "findings": findings,
    })


# ═══════════════════════════════════════════════════════════════════════
#  platform_read
# ═══════════════════════════════════════════════════════════════════════

async def platform_get_license_status(tenant_id: str = "") -> str:
    """Get current license state."""
    data = get_ae_client().get_license()
    return _safe_json({
        "tenant_id": tenant_id or get_ae_client().org,
        "license_type": data.get("licenseType") or data.get("type"),
        "status": data.get("status") or data.get("state"),
        "valid_until": data.get("validUntil") or data.get("expiryDate"),
        "max_agents": data.get("maxAgents") or data.get("agentLimit"),
        "max_workflows": data.get("maxWorkflows") or data.get("workflowLimit"),
        "current_agents": data.get("currentAgents") or data.get("activeAgents"),
        "features": data.get("features") or data.get("enabledFeatures"),
    })


async def platform_get_queue_depth(tenant_id: str = "") -> str:
    """Get queue depth summary across the platform."""
    data = get_ae_client().get_queue_depth()

    if isinstance(data, dict) and "queue_depth" not in data and "queues" not in data:
        return _safe_json({
            "tenant_id": tenant_id or get_ae_client().org,
            "total_pending": data.get("queue_depth") or data.get("pendingRequests") or data.get("pending"),
            "total_running": data.get("active_executions") or data.get("runningRequests") or data.get("running"),
            "total_retry": data.get("retryRequests") or data.get("retry"),
            "raw": data,
        })

    queues = data.get("queues") or []
    return _safe_json({
        "tenant_id": tenant_id or get_ae_client().org,
        "queues": queues,
        "total_pending": sum(q.get("pending", 0) for q in queues if isinstance(q, dict)),
    })


# ═══════════════════════════════════════════════════════════════════════
#  result_read
# ═══════════════════════════════════════════════════════════════════════

async def result_get_failure_category(request_id: str) -> str:
    """Classify the failure into a normalized category."""
    data = get_ae_client().get_request(request_id)
    error = (data.get("errorMessage") or data.get("errorDetails") or "").lower()
    status = (data.get("status") or "").upper()

    category = "UNKNOWN"
    if status not in ("FAILURE", "ERROR", "FAILED"):
        category = "NOT_FAILED"
    elif "credential" in error or "pool" in error or "login" in error:
        category = "CREDENTIAL_ISSUE"
    elif "timeout" in error or "timed out" in error:
        category = "TIMEOUT"
    elif "connection" in error or "network" in error or "unreachable" in error:
        category = "CONNECTIVITY"
    elif "permission" in error or "access denied" in error or "unauthorized" in error:
        category = "PERMISSION_DENIED"
    elif "not found" in error or "missing" in error:
        category = "RESOURCE_NOT_FOUND"
    elif "file" in error or "path" in error or "directory" in error:
        category = "FILE_SYSTEM"
    elif "input" in error or "parameter" in error or "validation" in error:
        category = "INPUT_VALIDATION"
    elif "memory" in error or "out of memory" in error:
        category = "RESOURCE_EXHAUSTION"
    elif "agent" in error:
        category = "AGENT_ISSUE"
    elif error:
        category = "APPLICATION_ERROR"

    return _safe_json({
        "request_id": request_id,
        "status": status,
        "failure_category": category,
        "error_message": data.get("errorMessage") or data.get("errorDetails"),
    })
