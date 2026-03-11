"""
P0 workflow_read (7) + workflow_mutate (4) tools — 11 total.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.workflow")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


async def workflow_list(limit: int = 100) -> str:
    """List all available workflows."""
    results = get_ae_client().search_workflows(limit=limit)
    items = []
    for w in results:
        items.append({
            "workflow_id": w.get("workflowId") or w.get("id"),
            "workflow_name": w.get("workflowName") or w.get("name"),
            "description": w.get("description"),
            "category": w.get("category"),
            "active": w.get("active", True),
        })
    return _safe_json({"workflows": items, "count": len(items)})


# ═══════════════════════════════════════════════════════════════════════
#  workflow_read
# ═══════════════════════════════════════════════════════════════════════

async def workflow_search(query: str = "", category: str = "", limit: int = 50) -> str:
    """Search workflows by name, description, or category."""
    filters = {}
    if category:
        filters["category"] = category
    results = get_ae_client().search_workflows(query=query, filters=filters, limit=limit)
    items = []
    for w in results[:limit]:
        items.append({
            "workflow_id": w.get("workflowId") or w.get("id"),
            "workflow_name": w.get("workflowName") or w.get("name"),
            "description": w.get("description"),
            "category": w.get("category"),
            "active": w.get("active", True),
        })
    return _safe_json({"results": items, "count": len(items)})


async def workflow_list_for_user(user_id: str) -> str:
    """Get workflows visible/accessible to a user."""
    results = get_ae_client().get_user_workflows(user_id)
    items = []
    for w in results:
        items.append({
            "workflow_id": w.get("workflowId") or w.get("id"),
            "workflow_name": w.get("workflowName") or w.get("name"),
            "description": w.get("description"),
            "active": w.get("active", True),
        })
    return _safe_json({"user_id": user_id, "workflows": items, "count": len(items)})


async def workflow_get_details(workflow_id: str) -> str:
    """Get full workflow configuration details."""
    data = get_ae_client().get_workflow(workflow_id)
    return _safe_json(data)


async def workflow_get_runtime_parameters(workflow_id: str) -> str:
    """Get the input parameter schema for a workflow."""
    data = get_ae_client().get_workflow_runtime_params(workflow_id)

    params = data
    if isinstance(data, dict):
        params = (
            data.get("parameters")
            or data.get("params")
            or data.get("inputParameters")
            or data
        )

    formatted = []
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict):
                formatted.append({
                    "name": p.get("name"),
                    "display_name": p.get("displayName") or p.get("displayname"),
                    "type": p.get("type") or p.get("dataType"),
                    "required": p.get("required") or p.get("optional") is False,
                    "default_value": p.get("defaultValue") or p.get("value"),
                    "description": p.get("description") or p.get("helpText"),
                })

    return _safe_json({
        "workflow_id": workflow_id,
        "parameters": formatted if formatted else params,
    })


async def workflow_get_flags(workflow_id: str) -> str:
    """Get monitoring, checkpoint, and logging flags for a workflow."""
    data = get_ae_client().get_workflow(workflow_id)
    return _safe_json({
        "workflow_id": workflow_id,
        "workflow_name": data.get("workflowName") or data.get("name"),
        "active": data.get("active"),
        "monitoring_enabled": data.get("monitoringEnabled") or data.get("monitoring"),
        "checkpoint_enabled": data.get("checkpointEnabled") or data.get("checkpoint"),
        "logging_level": data.get("loggingLevel") or data.get("logLevel"),
        "retry_enabled": data.get("retryEnabled"),
        "retry_count": data.get("retryCount") or data.get("maxRetries"),
        "timeout_minutes": data.get("timeoutMinutes") or data.get("timeout"),
    })


async def workflow_get_assignment_targets(workflow_id: str) -> str:
    """Get assigned agents and controllers for a workflow."""
    data = get_ae_client().get_workflow(workflow_id)
    return _safe_json({
        "workflow_id": workflow_id,
        "workflow_name": data.get("workflowName") or data.get("name"),
        "assigned_agents": data.get("assignedAgents") or data.get("agents") or [],
        "assigned_controllers": data.get("assignedControllers") or data.get("controllers") or [],
        "assignment_type": data.get("assignmentType") or data.get("agentAssignment"),
    })


async def workflow_get_permissions(workflow_id: str) -> str:
    """Get permission configuration for a workflow."""
    data = get_ae_client().get_workflow_permissions(workflow_id)
    return _safe_json({"workflow_id": workflow_id, "permissions": data})


# ═══════════════════════════════════════════════════════════════════════
#  workflow_mutate
# ═══════════════════════════════════════════════════════════════════════

async def workflow_disable(
    workflow_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Disable a workflow. Guarded operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "disable_workflow",
            "workflow_id": workflow_id,
            "reason": reason,
            "message": f"Would disable workflow {workflow_id}. No changes made.",
        })
    data = get_ae_client().disable_workflow(workflow_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "disable_workflow",
        "workflow_id": workflow_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


async def workflow_assign_to_agent(
    workflow_id: str,
    agent_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Assign a workflow to a specific agent. Guarded operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "assign_workflow_to_agent",
            "workflow_id": workflow_id,
            "agent_id": agent_id,
            "reason": reason,
            "message": f"Would assign workflow {workflow_id} to agent {agent_id}. No changes made.",
        })
    data = get_ae_client().assign_workflow_to_agent(workflow_id, agent_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "assign_workflow_to_agent",
        "workflow_id": workflow_id,
        "agent_id": agent_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


async def workflow_update_permissions(
    workflow_id: str,
    permissions: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Update access control permissions for a workflow. Privileged operation."""
    try:
        perm_dict = json.loads(permissions) if isinstance(permissions, str) else permissions
    except json.JSONDecodeError:
        return _safe_json({"error": "permissions must be valid JSON"})

    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "update_workflow_permissions",
            "workflow_id": workflow_id,
            "permissions": perm_dict,
            "reason": reason,
            "message": f"Would update permissions on workflow {workflow_id}. No changes made.",
        })
    data = get_ae_client().update_workflow_permissions(workflow_id, perm_dict, reason=reason)
    return _safe_json({
        "success": True,
        "action": "update_workflow_permissions",
        "workflow_id": workflow_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


async def workflow_rollback_version(
    workflow_id: str,
    version: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Rollback a workflow to a previous version. Privileged operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "rollback_workflow_version",
            "workflow_id": workflow_id,
            "version": version,
            "reason": reason,
            "message": f"Would rollback workflow {workflow_id} to version {version}. No changes made.",
        })
    data = get_ae_client().rollback_workflow_version(workflow_id, version, reason=reason)
    return _safe_json({
        "success": True,
        "action": "rollback_workflow_version",
        "workflow_id": workflow_id,
        "version": version,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ═══════════════════════════════════════════════════════════════════════
#  P1 support: workflow_get_by_id, get_recent_failure_stats, enable
# ═══════════════════════════════════════════════════════════════════════

async def workflow_get_by_id(workflow_id: str) -> str:
    """Fetch workflow record by ID (alias for get_details)."""
    data = get_ae_client().get_workflow(workflow_id)
    return _safe_json(data)


async def workflow_get_recent_failure_stats(workflow_id: str, time_range_hours: int = 24) -> str:
    """Failure trend for a workflow over time."""
    client = get_ae_client()
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(time_range_hours, 1))
    failures = client.search_requests(
        filters={"workflowName": workflow_id, "status": "Failure", "fromDate": int(cutoff.timestamp() * 1000)},
        limit=200,
    )
    return _safe_json({
        "workflow_id": workflow_id,
        "time_range_hours": time_range_hours,
        "failure_count": len(failures),
        "failures": [{"request_id": r.get("id") or r.get("automationRequestId"), "created": r.get("createdDate"), "error": r.get("errorMessage")} for r in failures[:20]],
    })


async def workflow_enable(
    workflow_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Enable a workflow. Guarded operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "enable_workflow",
            "workflow_id": workflow_id,
            "reason": reason,
            "message": f"Would enable workflow {workflow_id}. No changes made.",
        })
    data = get_ae_client().enable_workflow(workflow_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "enable_workflow",
        "workflow_id": workflow_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })
