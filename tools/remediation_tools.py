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


def restart_execution(execution_id: str,
                      workflow_name: str = "Unknown",
                      from_checkpoint: bool = True,
                      reason: str = "Restarted by support agent",
                      requested_by: str = None,
                      case_id: str = None,
                      dry_run: bool = False) -> dict:
    if workflow_name != "Unknown" and workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"Workflow '{workflow_name}' is protected and cannot be "
                f"restarted automatically. Escalate to the operations team."
            ),
        }
    
    if dry_run:
        return {
            "success": True,
            "message": f"[DRY RUN] Would restart request {execution_id}",
            "dry_run": True
        }

    client = get_ae_client()
    
    # 1. Resolve workflow name (internal use/protection only)
    if not workflow_name or workflow_name == "Unknown":
        try:
            status = client.get_execution_status(execution_id)
            workflow_name = status.get("workflowName") or "Unknown"
            logger.info(f"Resolved workflow name for {execution_id}: {workflow_name}")
        except Exception as e:
            logger.warning(f"Could not resolve workflow name for {execution_id}: {e}")

    # 2. Check Protection
    if workflow_name != "Unknown" and workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"Workflow '{workflow_name}' is protected and cannot be "
                f"restarted automatically. Escalate to the operations team."
            ),
        }

    # 3. Call updated restart_request (uses PUT /restart)
    try:
        resp = client.restart_request(execution_id, reason=reason)
        
        # Check for T4 success property
        if not resp.get("success", True):
            error_msg = resp.get("errorMessage") or resp.get("message") or "T4 returned failure."
            return {
                "success": False,
                "error": f"Restart failed: {error_msg}",
                "raw": resp
            }
            
        return {
            "success": True,
            "message": resp.get("message") or f"Request {execution_id} has been restarted",
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "raw": resp
        }
    except Exception as e:
        err_str = str(e)
        # AE-2624: T4 restart limit reached (max 10 restarts per instance)
        # Automatically fall back to resubmit which creates a fresh execution
        if "AE-2624" in err_str or "maximum limit of 10 restarts" in err_str.lower():
            logger.warning(
                f"AE-2624 restart limit reached for {execution_id}. "
                f"Automatically falling back to resubmit_execution."
            )
            try:
                resubmit_resp = resubmit_execution(
                    execution_id=execution_id,
                    from_failure_point=True,
                    reason=f"{reason} (auto-resubmit: restart limit AE-2624 reached)"
                )
                return {
                    **resubmit_resp,
                    "restart_limit_reached": True,
                    "fallback": "resubmit",
                    "hint": (
                        "The restart limit (10) for this execution was reached. "
                        "The system automatically resubmitted it as a new execution instead."
                    ),
                }
            except Exception as resubmit_err:
                return {
                    "success": False,
                    "error": f"Restart limit reached (AE-2624) and resubmit also failed: {resubmit_err}",
                    "hint": "The restart limit of 10 has been reached and resubmit also failed. Please manually trigger a new execution from the AutomationEdge UI.",
                    "restart_limit_reached": True,
                }

        logger.error(f"Restart failed for {execution_id}: {e}")
        return {
            "success": False,
            "error": f"Restart failed: {err_str}",
            "hint": "Ensure the execution is in a failed state. For terminal states, try 'resubmit_execution' instead.",
        }


def resubmit_execution(execution_id: str,
                       from_failure_point: bool = True,
                       reason: str = "Resubmitted by support agent") -> dict:
    """Resubmit a failed execution as a NEW run.

    This is DIFFERENT from restart_execution:
    - restart_execution: resumes the SAME execution (PUT /restart)
    - resubmit_execution: creates a NEW execution (POST /resubmit)

    Use from_failure_point=True to resubmit from the last failure step,
    or from_failure_point=False to resubmit from the very beginning.
    """
    try:
        resp = get_ae_client().resubmit_request(
            execution_id, reason=reason, from_failure_point=from_failure_point
        )
        mode = "from failure point" if from_failure_point else "from start"
        return {
            "success": True,
            "message": resp.get("message") or f"Request {execution_id} has been resubmitted ({mode})",
            "execution_id": execution_id,
            "from_failure_point": from_failure_point,
            "raw": resp
        }
    except Exception as e:
        logger.error(f"Resubmit failed for {execution_id}: {e}")
        return {
            "success": False,
            "error": f"Resubmit failed: {str(e)}"
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
            "Restart a failed workflow execution or request. "
            "Pass the execution_id (request id) to trigger the restart. "
            "Use this for ANY request to 'restart', 'retry', or 'run again'."
        ),
        category="remediation",
        tier="low_risk",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "Failed execution ID (request id)",
            },
            "workflow_name": {
                "type": "string",
                "description": "Workflow name (optional if execution_id is known)",
            },
            "from_checkpoint": {
                "type": "boolean",
                "description": "Resume from checkpoint (default true)",
            },
            "reason": {
                "type": "string",
                "description": "Reason for restart",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Simulate restart without executing",
            },
        },
        required_params=["execution_id"],
    ),
    restart_execution,
)

tool_registry.register(
    ToolDefinition(
        name="resubmit_execution",
        description=(
            "Resubmit a failed execution as a NEW run. "
            "DIFFERENT from restart_execution: restart resumes the SAME execution; "
            "resubmit creates a NEW execution. "
            "Use when: user says 'resubmit', 'run again from scratch', or 'create new run'. "
            "Use from_failure_point=True to retry from where it failed, "
            "or from_failure_point=False to start fresh from the beginning."
        ),
        category="remediation",
        tier="low_risk",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The failed execution ID (request id) to resubmit",
            },
            "from_failure_point": {
                "type": "boolean",
                "description": "If true, resubmit from the last failure step. If false, resubmit from start. Default: true",
            },
            "reason": {
                "type": "string",
                "description": "Reason for resubmitting",
            },
        },
        required_params=["execution_id"],
        use_when=(
            "User explicitly asks to 'resubmit', 'run again from scratch', or when "
            "restart fails and a fresh execution is needed."
        ),
        avoid_when=(
            "User says 'restart' — use restart_execution instead."
        ),
    ),
    resubmit_execution,
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
        always_available=True,
        use_when=(
            "You know the target workflow and need to execute it using the "
            "workflow name plus collected runtime parameters."
        ),
        avoid_when=(
            "A safer read-only diagnostic tool can answer the question without "
            "starting automation."
        ),
        input_examples=[
            {
                "workflow_name": "Claims_Processing_Daily",
                "parameters": {"businessDate": "2026-03-07", "region": "west"},
            }
        ],
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
