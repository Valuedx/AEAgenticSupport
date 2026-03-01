"""
Remediation tools — restart, trigger, requeue operations.
Higher-risk actions require approval via the ApprovalGate.
"""

import logging

from config.settings import CONFIG
from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.remediation")


def restart_execution(workflow_name: str, execution_id: str,
                      from_checkpoint: bool = True) -> dict:
    if workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"Workflow '{workflow_name}' is protected and cannot be "
                f"restarted automatically. Escalate to the operations team."
            ),
        }
    resp = get_ae_client().post(
        f"/api/v1/executions/{execution_id}/restart",
        payload={
            "workflow_name": workflow_name,
            "from_checkpoint": from_checkpoint,
        },
    )
    return {
        "success": True,
        "new_execution_id": resp.get("new_execution_id"),
        "workflow_name": workflow_name,
        "restarted_from": "checkpoint" if from_checkpoint else "beginning",
        "status": resp.get("status"),
    }


def trigger_workflow(workflow_name: str, parameters: dict = None) -> dict:
    if workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"Workflow '{workflow_name}' is protected. "
                f"Manual trigger required."
            ),
        }
    resp = get_ae_client().post(
        f"/api/v1/workflows/{workflow_name}/trigger",
        payload={"parameters": parameters or {}},
    )
    return {
        "success": True,
        "execution_id": resp.get("execution_id"),
        "workflow_name": workflow_name,
        "status": resp.get("status"),
    }


def requeue_item(queue_name: str, item_id: str) -> dict:
    resp = get_ae_client().post(
        f"/api/v1/queues/{queue_name}/items/{item_id}/requeue"
    )
    return {
        "success": True,
        "queue_name": queue_name,
        "item_id": item_id,
        "new_status": resp.get("status", "queued"),
    }


def bulk_retry_failures(workflow_name: str = "", hours: int = 24,
                        max_retries: int = None) -> dict:
    max_ops = max_retries or CONFIG.get("MAX_BULK_OPERATIONS", 10)
    resp = get_ae_client().post(
        "/api/v1/executions/bulk-retry",
        payload={
            "workflow_name": workflow_name,
            "hours": hours,
            "max_retries": max_ops,
        },
    )
    return {
        "success": True,
        "retried_count": resp.get("retriedCount", 0),
        "skipped_count": resp.get("skippedCount", 0),
        "errors": resp.get("errors", []),
    }


def disable_workflow(workflow_name: str, reason: str = "") -> dict:
    resp = get_ae_client().post(
        f"/api/v1/workflows/{workflow_name}/disable",
        payload={"reason": reason},
    )
    return {
        "success": True,
        "workflow_name": workflow_name,
        "status": resp.get("status"),
        "reason": reason,
    }


# ── Register remediation tools ──

tool_registry.register(
    ToolDefinition(
        name="restart_execution",
        description=(
            "Restart a failed workflow execution, optionally from the "
            "last checkpoint to avoid re-processing completed steps."
        ),
        category="remediation",
        tier="low_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow name",
            },
            "execution_id": {
                "type": "string",
                "description": "Failed execution ID",
            },
            "from_checkpoint": {
                "type": "boolean",
                "description": "Resume from checkpoint (default true)",
            },
        },
        required_params=["workflow_name", "execution_id"],
    ),
    restart_execution,
)

tool_registry.register(
    ToolDefinition(
        name="trigger_workflow",
        description=(
            "Trigger a new execution of a workflow with optional parameters."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow to trigger",
            },
            "parameters": {
                "type": "object",
                "description": "Optional workflow parameters",
            },
        },
        required_params=["workflow_name"],
    ),
    trigger_workflow,
)

tool_registry.register(
    ToolDefinition(
        name="requeue_item",
        description="Requeue a failed queue item for reprocessing.",
        category="remediation",
        tier="low_risk",
        parameters={
            "queue_name": {
                "type": "string",
                "description": "Queue name",
            },
            "item_id": {
                "type": "string",
                "description": "Item ID to requeue",
            },
        },
        required_params=["queue_name", "item_id"],
    ),
    requeue_item,
)

tool_registry.register(
    ToolDefinition(
        name="bulk_retry_failures",
        description=(
            "Retry all failed executions within a time window. "
            "Use with caution — high impact operation."
        ),
        category="remediation",
        tier="high_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Filter by workflow (empty for all)",
            },
            "hours": {
                "type": "integer",
                "description": "Time window in hours (default 24)",
            },
            "max_retries": {
                "type": "integer",
                "description": "Max retries to attempt",
            },
        },
        required_params=[],
    ),
    bulk_retry_failures,
)

tool_registry.register(
    ToolDefinition(
        name="disable_workflow",
        description=(
            "Disable a workflow to prevent future scheduled runs. "
            "Use when a workflow is causing cascading failures."
        ),
        category="remediation",
        tier="high_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow to disable",
            },
            "reason": {
                "type": "string",
                "description": "Reason for disabling",
            },
        },
        required_params=["workflow_name"],
    ),
    disable_workflow,
)
