"""
Remediation tools — restart, trigger, requeue operations.
Higher-risk actions require approval via the ApprovalGate.
"""

import logging
import json

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
    
    # ── "Ask Again" pattern ──
    # Identify specific required parameters from the local catalog (one query for id + params)
    client = get_ae_client()
    _, schema = client.get_cached_workflow_info(workflow_name)
    required = []
    for p in schema:
        name = p.get("name")
        if not name:
            continue
        opt = p.get("optional")
        optional_false = opt is False or (
            isinstance(opt, str) and opt.strip().lower() in {"false", "0", "no", "n"}
        )
        if p.get("required") or p.get("is_required") or optional_false:
            required.append(name)
    missing = [p for p in required if not (parameters or {}).get(p)]

    if missing:
        # Build a friendly, specific question (mirrors dynamic tool behavior)
        param_bullets = "\n".join(f"  • {p}" for p in missing)
        friendly_name = workflow_name.replace("_", " ").replace("-", " ").title()
        return {
            "success": False,
            "needs_user_input": True,
            "question": (
                f"I'm ready to help with **{friendly_name}**! Just need a few specific details first:\n"
                f"{param_bullets}\n\n"
                f"Please share these and I'll take care of the rest."
            ),
            "tool_name": "trigger_workflow",
            "workflow_name": workflow_name,
            "missing_params": missing
        }

    # Use the updated T4-compatible execute_workflow method
    try:
        workflow_id, _ = client.get_cached_workflow_info(workflow_name)
        if not workflow_id:
            workflow_id = workflow_name
        raw = client.execute_workflow(
            workflow_name=workflow_name,
            workflow_id=workflow_id,
            params=parameters,
            source="ops-agent-remediation"
        )
        
        # Strict validation: T4 usually returns a Request ID
        req_id = (
            raw.get("automationRequestId") 
            or raw.get("requestId") 
            or raw.get("id")
        )

        # If we got a 200/201 but the body says success=False or has no ID, it's a failure
        if not req_id and not raw.get("success", True):
            error_msg = raw.get("errorMessage") or raw.get("message") or "T4 returned failure without details."
            return {"success": False, "error": f"T4 execution failed: {error_msg}", "raw": raw}

        if not req_id:
            logger.warning(f"T4 trigger for {workflow_name} succeeded but returned no Request ID.")

        # Poll execution status every 2 seconds for a short window, then decide response.
        final_status = raw.get("status") or raw.get("state") or "QUEUED"
        poll_raw = {}
        if req_id:
            try:
                poll = client.poll_execution_status(
                    execution_id=str(req_id),
                    poll_interval_sec=2,
                    max_attempts=15,
                )
                final_status = poll.get("status", final_status)
                poll_raw = poll.get("raw") or {}
            except Exception as poll_exc:
                logger.warning("Status poll failed for %s (%s): %s", workflow_name, req_id, poll_exc)

        # Pull friendly details from workflowResponse if available.
        detail_msg = ""
        wf_response = (poll_raw or {}).get("workflowResponse")
        if wf_response:
            try:
                parsed = json.loads(wf_response)
                detail_msg = str(parsed.get("message") or "").strip()
            except Exception:
                pass

        status_upper = str(final_status or "").upper()
        if status_upper in {"COMPLETE"}:
            return {
                "success": True,
                "execution_id": req_id,
                "workflow_name": workflow_name,
                "status": "Complete",
                "message": detail_msg or f"{workflow_name} completed successfully.",
                "request_id": req_id,
                "raw": poll_raw or raw,
            }

        if status_upper in {"FAILURE", "ERROR"}:
            error_msg = (
                (poll_raw or {}).get("errorMessage")
                or (poll_raw or {}).get("errorDetails")
                or detail_msg
                or f"{workflow_name} execution failed."
            )
            return {
                "success": False,
                "execution_id": req_id,
                "workflow_name": workflow_name,
                "status": final_status,
                "error": error_msg,
                "request_id": req_id,
                "raw": poll_raw or raw,
            }

        # Pending / queued / new / timeout / no_agent / in_progress path.
        healthy_agent = None
        if final_status == "in_progress" and poll.get("in_progress_hint"):
            pending_msg = poll.get("in_progress_hint")
        else:
            agent_data = client.check_agent_status()
            healthy_agent = next(
                (a for a in agent_data if str(a.get("agentState", "")).upper() in {"CONNECTED", "RUNNING", "ACTIVE"}),
                None,
            )
            if status_upper in {"NO_AGENT"} or not healthy_agent:
                pending_msg = (
                    "The request is still pending and no active automation agent is available right now. "
                    "Please contact your administrator to start or reconnect the agent."
                )
            else:
                pending_msg = (
                    "Your request has been accepted and is currently queued (pending execution). "
                    "The agent is online; execution should start shortly."
                )

        return {
            "success": True,
            "execution_id": req_id,
            "workflow_name": workflow_name,
            "status": final_status or "QUEUED",
            "message": pending_msg,
            "request_id": req_id,
            "raw": poll_raw or raw,
            "agent_status": healthy_agent.get("agentState") if healthy_agent else "UNAVAILABLE",
        }
    except Exception as e:
        logger.error(f"Failed to trigger workflow {workflow_name}: {e}")
        return {
            "success": False,
            "error": str(e)
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
            "Trigger a new execution of a workflow with required parameters."
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
                "description": "Workflow input parameters (as a dictionary)",
            },
        },
        required_params=["workflow_name", "parameters"],
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
