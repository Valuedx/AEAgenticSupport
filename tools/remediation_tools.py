"""
Remediation tools — restart, trigger, requeue operations.
Higher-risk actions require approval via the ApprovalGate.

CHANGES in trigger_workflow (v2):
  1. Better parameter collection UX       — batch-collects ALL missing params in one shot,
                                            with per-param type hints and examples.
  2. Smarter workflow name resolution      — fuzzy/alias resolution before schema lookup;
                                            unknown names surface a friendly suggestion.
  3. Improved status/response messaging   — distinct human-readable messages for every
                                            terminal and non-terminal state; no raw dict dumps.
  4. Approval gate flow improvements      — clean, card-style confirmation message instead of
                                            raw dict; risk badge and parameter table.
  5. File-parameter early exit            — detected BEFORE asking for any other params so the
                                            user is never asked for inputs they can't supply.
  6. Agent-status pre-check               — ensures at least one assigned automation agent is
                                            active before proceeding with parameter collection.
"""

import logging
import json

from config.settings import CONFIG
from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.remediation")


# ─────────────────────────────────────────────────────────────────────────────
# trigger_workflow helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_approval_message(workflow_name: str, parameters: dict, risk: str = "medium_risk") -> str:
    """
    Return a clean, card-style approval prompt instead of a raw dict dump.

    Example output
    ──────────────
    🤖 **Ready to trigger: Death_Claim**
    🟡 Risk level: Medium

    | Parameter  | Value                                              |
    |------------|----------------------------------------------------|
    | Input_Path | C:\\Users\\omkar.patil\\Downloads\\cti_template.csv |

    Reply **approve** to proceed or **reject** to cancel.
    """
    risk_labels = {
        "low_risk":    "🟢 Low",
        "medium_risk": "🟡 Medium",
        "high_risk":   "🔴 High",
    }
    risk_display = risk_labels.get(risk, "🟡 Medium")

    if parameters:
        rows = "\n".join(f"| `{k}` | {v} |" for k, v in parameters.items())
        param_table = (
            "\n| Parameter | Value |\n"
            "|-----------|-------|\n"
            f"{rows}\n"
        )
    else:
        param_table = "\n_No parameters required._\n"

    return (
        f"🤖 **Ready to trigger: {workflow_name}**\n"
        f"{risk_display} Risk level: {risk_display}\n"
        f"{param_table}\n"
        "Reply **approve** to proceed or **reject** to cancel."
    )


def _friendly_status_message(
    status: str,
    workflow_name: str,
    req_id,
    detail_msg: str = "",
    healthy_agent=None,
) -> str:
    """Map any raw AE status string to a human-readable sentence."""
    s = str(status or "").upper()

    if s == "COMPLETE":
        return detail_msg or f"✅ **{workflow_name}** completed successfully. (Request ID: `{req_id}`)"

    if s in {"FAILURE", "ERROR"}:
        base = detail_msg or f"❌ **{workflow_name}** failed during execution."
        return (
            f"{base}\n\n"
            f"Request ID: `{req_id}`. "
            "You can retry with `restart_execution` or `resubmit_execution`."
        )

    if s == "IN_PROGRESS":
        return (
            f"⏳ **{workflow_name}** is currently running. "
            f"Request ID: `{req_id}`. Check back shortly for the final status."
        )

    if s == "TIMEOUT":
        return (
            f"⏱️ **{workflow_name}** timed out waiting for a status update. "
            f"Request ID: `{req_id}`. "
            "The bot may still be running — please verify in the AE portal."
        )

    if s == "NO_AGENT":
        return (
            f"🚫 **{workflow_name}** could not start — no automation agent is currently available. "
            "Please ask your administrator to start or reconnect an agent, then retry."
        )

    # QUEUED / PENDING / NEW or anything unrecognised
    if healthy_agent:
        return (
            f"📋 **{workflow_name}** has been accepted and is queued for execution. "
            f"Request ID: `{req_id}`. An agent is online; it should start shortly."
        )
    return (
        f"📋 **{workflow_name}** has been queued. Request ID: `{req_id}`. "
        "No active agent was detected — contact your administrator if it doesn't start soon."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Remediation functions
# ─────────────────────────────────────────────────────────────────────────────

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
                    "hint": (
                        "The restart limit of 10 has been reached and resubmit also failed. "
                        "Please manually trigger a new execution from the AutomationEdge UI."
                    ),
                    "restart_limit_reached": True,
                }

        logger.error(f"Restart failed for {execution_id}: {e}")
        return {
            "success": False,
            "error": f"Restart failed: {err_str}",
            "hint": (
                "Ensure the execution is in a failed state. "
                "For terminal states, try 'resubmit_execution' instead."
            ),
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
    """
    Trigger a new execution of a bot (workflow) with required parameters.

    Improvements over v1
    --------------------
    1. File-param guard fires FIRST — user never gets asked for text params they
       can't supply if the workflow also needs a file upload.
    2. Fuzzy/alias name resolution with a helpful suggestion when the name is unknown.
    3. ALL missing parameters are collected in a single, clearly formatted question
       (type hints + examples) instead of one-at-a-time prompts.
    4. Clean card-style approval message (no raw dict dump).
    5. Every status code maps to a distinct, human-readable message.
    6. Agent-status pre-check — ensures at least one assigned automation agent is
       active before proceeding with parameter collection.
    """
    parameters = parameters or {}
    client = get_ae_client()

    # ── IMPROVEMENT 2: Smarter workflow name resolution ───────────────────────
    resolved_name = client.resolve_cached_workflow_name(workflow_name)

    if not resolved_name:
        # Try to surface similar workflow names so the user can correct themselves
        try:
            all_workflows = client.list_workflow_names()  # returns list[str]
            workflow_name_lower = workflow_name.lower().replace(" ", "_")
            suggestions = [
                w for w in all_workflows
                if workflow_name_lower in w.lower() or w.lower() in workflow_name_lower
            ]
        except Exception:
            suggestions = []

        hint = (
            "\n\nDid you mean one of these?\n" + "\n".join(f"  • {s}" for s in suggestions[:5])
            if suggestions
            else ""
        )
        return {
            "success": False,
            "needs_user_input": True,
            "question": (
                f"I couldn't find a workflow matching **\"{workflow_name}\"**. "
                f"Please double-check the bot name and try again.{hint}"
            ),
            "workflow_name": workflow_name,
        }

    # Protected-workflow guard (unchanged)
    if resolved_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"⛔ **{resolved_name}** is a protected workflow and cannot be "
                "triggered automatically. Please request a manual trigger from the operations team."
            ),
        }

    # ── IMPROVEMENT 5: File-param guard fires BEFORE param collection ─────────
    # AE-77: Check for "File" type parameters — file upload not supported in agentic chat.
    schema = client.get_cached_workflow_parameters(resolved_name)
    file_params = [
        p.get("name") for p in schema
        if str(p.get("type") or p.get("uiControlType") or "").strip().lower()
        in {"file", "attachment", "upload"}
    ]
    if file_params:
        logger.info(f"Workflow '{resolved_name}' requires file upload — rejecting agentic trigger.")
        param_list = ", ".join(f"`{p}`" for p in file_params)
        return {
            "success": False,
            "error": (
                f"📎 I've identified that **{resolved_name}** requires a **file upload** "
                f"for the following parameter(s): {param_list}.\n\n"
                "Since file uploading is not supported via chat, please log in to the "
                "**AutomationEdge (AE) portal** to trigger this bot manually.\n\n"
                "Thank you for your patience!"
            ),
            "reason": f"Workflow requires file upload for: {', '.join(file_params)}",
            "workflow_name": resolved_name,
        }

    # ── IMPROVEMENT 6: Agent status check ─────────────────────────────────────
    # Check if at least one agent assigned to this workflow is RUNNING/CONNECTED.
    try:
        wf_agents = client.get_workflow_agents()
        # Find the entry for our resolved_name
        wf_entry = next((item for item in wf_agents if item.get("workflow", {}).get("name") == resolved_name), None)
        if wf_entry:
            agents = wf_entry.get("agents", [])
            running_agents = [
                a for a in agents 
                if str(a.get("agentState", "")).upper() in {"RUNNING", "CONNECTED", "ACTIVE"}
            ]
            if not running_agents:
                agent_names = ", ".join([a.get("agentName", "Unknown") for a in agents]) or "No agents assigned"
                return {
                    "success": False,
                    "error": (
                        f"🚫 **{resolved_name}** cannot be triggered because all assigned agents are currently offline or stopped ({agent_names}).\n\n"
                        "Please start at least one assigned agent in the AutomationEdge portal before proceeding."
                    ),
                    "workflow_name": resolved_name,
                }
    except Exception as exc:
        logger.warning(f"Pre-trigger agent check failed for {resolved_name}: {exc}")

    # ── IMPROVEMENT 1: Batch parameter collection ─────────────────────────────
    required = client.get_required_parameters(resolved_name)       # list[str]
    param_schema_map = {p.get("name"): p for p in schema}         # name → schema entry

    missing = [p for p in required if not parameters.get(p)]

    if missing:
        param_lines = []
        for p in missing:
            meta = param_schema_map.get(p, {})
            p_type    = meta.get("type") or meta.get("uiControlType") or "text"
            p_example = meta.get("example") or meta.get("defaultValue") or ""
            example_str = f" _(e.g. `{p_example}`)_" if p_example else ""
            param_lines.append(f"  • **{p}** `[{p_type}]`{example_str}")

        friendly_name = resolved_name.replace("_", " ").replace("-", " ").title()
        return {
            "success": False,
            "needs_user_input": True,
            "question": (
                f"I'm ready to trigger **{friendly_name}**! "
                f"Please provide the following {'detail' if len(missing) == 1 else 'details'}:\n\n"
                + "\n".join(param_lines)
                + "\n\nShare all of them together and I'll kick it off right away."
            ),
            "tool_name": "trigger_workflow",
            "workflow_name": resolved_name,
            "missing_params": missing,
        }

    # ── IMPROVEMENT 4: Pre-build clean approval message for the gate ──────────
    # ApprovalGate middleware reads `approval_message` from the return dict.
    # We inject a formatted preview here so whatever the gate shows is human-friendly.
    approval_preview = _build_approval_message(
        workflow_name=resolved_name,
        parameters=parameters,
        risk="medium_risk",
    )

    # ── Execute ───────────────────────────────────────────────────────────────
    try:
        workflow_id, _ = client.get_cached_workflow_info(resolved_name)
        if not workflow_id:
            workflow_id = resolved_name

        raw = client.execute_workflow(
            workflow_name=resolved_name,
            workflow_id=workflow_id,
            params=parameters,
            source="ops-agent-remediation",
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
            return {
                "success": False,
                "error": f"❌ Trigger failed: {error_msg}",
                "raw": raw,
            }

        if not req_id:
            logger.warning(f"T4 trigger for '{resolved_name}' succeeded but returned no Request ID.")

        # ── Poll execution status ─────────────────────────────────────────────
        final_status = raw.get("status") or raw.get("state") or "QUEUED"
        poll_raw = {}
        poll_result = {}  # always defined so later references are safe

        if req_id:
            try:
                poll_result = client.poll_execution_status(
                    execution_id=str(req_id),
                    poll_interval_sec=2,
                    max_attempts=15,
                )
                final_status = poll_result.get("status", final_status)
                poll_raw = poll_result.get("raw") or {}
            except Exception as poll_exc:
                logger.warning("Status poll failed for %s (%s): %s", resolved_name, req_id, poll_exc)

        # Pull friendly details from workflowResponse if available
        detail_msg = ""
        wf_response = poll_raw.get("workflowResponse")
        if wf_response:
            try:
                parsed = json.loads(wf_response)
                detail_msg = str(parsed.get("message") or "").strip()
            except Exception:
                pass

        # ── IMPROVEMENT 3: Terminal failure path ──────────────────────────────
        status_upper = str(final_status or "").upper()
        if status_upper in {"FAILURE", "ERROR"}:
            error_msg = (
                poll_raw.get("errorMessage")
                or poll_raw.get("errorDetails")
                or detail_msg
                or ""
            )
            return {
                "success": False,
                "execution_id": req_id,
                "workflow_name": resolved_name,
                "status": final_status,
                "error": _friendly_status_message(
                    status=final_status,
                    workflow_name=resolved_name,
                    req_id=req_id,
                    detail_msg=error_msg,
                ),
                "request_id": req_id,
                "raw": poll_raw or raw,
            }

        # ── IMPROVEMENT 3: Non-terminal / pending path ────────────────────────
        healthy_agent = None
        if status_upper != "COMPLETE":
            in_progress_hint = poll_result.get("in_progress_hint") if poll_result else None
            if status_upper == "IN_PROGRESS" and in_progress_hint:
                pending_msg = in_progress_hint
            else:
                try:
                    agent_data = client.check_agent_status()
                    healthy_agent = next(
                        (
                            a for a in agent_data
                            if str(a.get("agentState", "")).upper() in {"CONNECTED", "RUNNING", "ACTIVE"}
                        ),
                        None,
                    )
                except Exception as agent_exc:
                    logger.warning("Agent status check failed: %s", agent_exc)

                pending_msg = _friendly_status_message(
                    status=final_status,
                    workflow_name=resolved_name,
                    req_id=req_id,
                    detail_msg=detail_msg,
                    healthy_agent=healthy_agent,
                )
        else:
            pending_msg = _friendly_status_message(
                status=final_status,
                workflow_name=resolved_name,
                req_id=req_id,
                detail_msg=detail_msg,
            )

        return {
            "success": True,
            "execution_id": req_id,
            "workflow_name": resolved_name,
            "status": final_status or "QUEUED",
            "message": pending_msg,
            "approval_message": approval_preview,   # clean gate message
            "request_id": req_id,
            "raw": poll_raw or raw,
            "agent_status": healthy_agent.get("agentState") if healthy_agent else "UNAVAILABLE",
        }

    except Exception as e:
        logger.error(f"Failed to trigger workflow '{resolved_name}': {e}")
        return {
            "success": False,
            "error": (
                f"❌ An unexpected error occurred while triggering **{resolved_name}**: {e}\n\n"
                "Please try again or contact your administrator."
            ),
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


def disable_schedule(schedule_id: str, reason: str = "") -> dict:
    """Disable or pause a schedule. Use when asked 'How can I pause or disable this schedule?'."""
    resp = get_ae_client().disable_schedule(schedule_id, reason=reason)
    return {
        "success": True,
        "schedule_id": schedule_id,
        "message": resp.get("message") or f"Schedule {schedule_id} disabled successfully",
        "raw": resp
    }


def enable_schedule(schedule_id: str, reason: str = "") -> dict:
    """Enable or resume a schedule. Use when asked 'How do I resume or enable this schedule?'."""
    resp = get_ae_client().enable_schedule(schedule_id, reason=reason)
    return {
        "success": True,
        "schedule_id": schedule_id,
        "message": resp.get("message") or f"Schedule {schedule_id} enabled successfully",
        "raw": resp
    }


# ─────────────────────────────────────────────────────────────────────────────
# Register all remediation tools
# ─────────────────────────────────────────────────────────────────────────────

tool_registry.register(
    ToolDefinition(
        name="restart_execution",
        description=(
            "Restart a failed bot (workflow) execution or request. "
            "Pass the execution_id (request id) to trigger the restart. "
            "Use this for ANY request to 'restart', 'retry', or 'run again' a bot."
        ),
        category="remediation",
        tier="medium_risk",
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
            "Resubmit a failed bot (workflow) execution as a NEW run. "
            "DIFFERENT from restart_execution: restart resumes the SAME execution; "
            "resubmit creates a NEW execution. "
            "Use when: user says 'resubmit', 'run again from scratch', or 'create new bot run'. "
            "Use from_failure_point=True to retry from where it failed, "
            "or from_failure_point=False to start fresh from the beginning."
        ),
        category="remediation",
        tier="medium_risk",
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
            "Trigger a new execution of a bot (workflow) with required parameters. "
            "Collects all missing parameters in one step, resolves fuzzy workflow names, "
            "detects file-upload requirements early, and returns clean human-readable "
            "status messages."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "The name (or partial/alias) of the bot (workflow) to trigger",
            },
            "parameters": {
                "type": "object",
                "description": "Workflow input parameters as a dictionary",
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
            "Retry all failed bot (workflow) executions within a time window. "
            "Use with caution — high impact operation."
        ),
        category="remediation",
        tier="high_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Filter by bot/workflow name (empty for all)",
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
            "Disable a bot (workflow) to prevent future scheduled runs. "
            "Use when a bot is causing cascading failures."
        ),
        category="remediation",
        tier="high_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "The bot (workflow) to disable",
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

# tool_registry.register(
#     ToolDefinition(
#         name="ae.schedule.disable",
#         description="Disable or pause a schedule. Use when asked 'How can I pause or disable this schedule?'. Needs a schedule_id.",
#         category="remediation",
#         tier="high_risk",
#         parameters={
#             "schedule_id": {"type": "string", "description": "Schedule ID to disable"},
#             "reason": {"type": "string", "description": "Reason for disabling", "optional": True},
#         },
#         required_params=["schedule_id"],
#     ),
#     disable_schedule,
# )

# tool_registry.register(
#     ToolDefinition(
#         name="ae.schedule.enable",
#         description="Enable or resume a schedule. Use when asked 'How do I resume or enable this schedule?'. Needs a schedule_id.",
#         category="remediation",
#         tier="high_risk",
#         parameters={
#             "schedule_id": {"type": "string", "description": "Schedule ID to enable"},
#             "reason": {"type": "string", "description": "Reason for enabling", "optional": True},
#         },
#         required_params=["schedule_id"],
#     ),
#     enable_schedule,
# )