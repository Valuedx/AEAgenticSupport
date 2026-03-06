"""
Remediation tools — restart, trigger, requeue operations.
Higher-risk actions require approval via the ApprovalGate.
"""

import logging
import json
import subprocess
import time
from pathlib import Path
import os

from config.settings import CONFIG
from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.remediation")


def _resolve_agent_startup_cmd() -> tuple[list[str], str]:
    """Resolve the best startup command and working directory."""
    raw_path = str(CONFIG.get("AGENT_STARTUP_PATH", "")).strip()
    if not raw_path:
        return ([], "")
    path = Path(raw_path)
    cwd = str(path) if path.is_dir() else str(path.parent)

    # If a file is provided, run it directly.
    if path.is_file():
        return (["cmd", "/c", "start", "", str(path)], cwd)

    # Directory provided: prefer startup scripts, then exe.
    candidates = [
        path / "startup.bat",
        path / "startup-debug.bat",
        path / "service.bat",
        path / "remoteStartup.bat",
        path / "aeagent.exe",
        path / "aeagent-service.exe",
        path / "aeagent-servicew.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return (["cmd", "/c", "start", "", str(candidate)], str(path))

    return ([], cwd)


def _resolve_agent_log_dir() -> Path | None:
    raw_path = str(CONFIG.get("AGENT_STARTUP_PATH", "")).strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    base = path.parent if path.is_file() else path
    if base.name.lower() == "bin":
        candidate = base.parent / "logs"
    else:
        candidate = base / "logs"
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def _tail_lines(path: Path, max_lines: int = 20, max_bytes: int = 32768) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        return lines[-max_lines:]
    except Exception:
        return []


def _collect_agent_log_tail(max_lines: int = 20) -> dict:
    log_dir = _resolve_agent_log_dir()
    if not log_dir:
        return {}
    logs = {}
    for name in ("aeagent.log", "health.log", "catalina.log"):
        path = log_dir / name
        if path.exists():
            tail = _tail_lines(path, max_lines=max_lines)
            if tail:
                logs[name] = tail
    return logs


def restart_ae_agent(agent_name: str = "", poll_interval_sec: int = 2, max_poll_attempts: int = 15) -> dict:
    """Start the AE agent process using the configured startup path and poll until running."""
    cmd, cwd = _resolve_agent_startup_cmd()
    if not cmd:
        return {"success": False, "error": "AGENT_STARTUP_PATH is not configured."}

    stdout = ""
    stderr = ""
    timed_out = False

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=10,
        )
        stdout = (result.stdout or "")[-500:]
        stderr = (result.stderr or "")[-500:]
        if result.returncode != 0:
            logger.warning(
                "restart_ae_agent failed (exit=%s). stdout=%s stderr=%s",
                result.returncode,
                stdout,
                stderr,
            )
            return {
                "success": False,
                "error": f"Agent restart failed with exit code {result.returncode}",
                "stdout": stdout,
                "stderr": stderr,
            }
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "")[-500:]
        stderr = (exc.stderr or "")[-500:]
        logger.warning(
            "restart_ae_agent start command timed out. stdout=%s stderr=%s",
            stdout,
            stderr,
        )
    except Exception as exc:
        logger.warning("restart_ae_agent failed to start: %s", exc, exc_info=True)
        return {"success": False, "error": f"Failed to start agent: {exc}"}

    client = get_ae_client()
    try:
        client_timeout = int(getattr(client, "timeout", 30) or 30)
    except Exception:
        client_timeout = 30
    poll_timeout = min(5, client_timeout)
    max_total_seconds = 60
    max_attempts_by_timeout = max(1, int(max_total_seconds / max(1, poll_timeout)))
    max_poll_attempts = min(max_poll_attempts, max_attempts_by_timeout)
    healthy = None
    last_state = "UNKNOWN"
    agents = []
    poll_count = 0
    if max_poll_attempts <= 0:
        return {
            "success": True,
            "agent_name": agent_name or "",
            "message": "Restart command dispatched. Checking status next.",
        }

    for _ in range(max_poll_attempts):
        time.sleep(max(1, int(poll_interval_sec)))
        poll_count += 1
        logger.info("restart_ae_agent poll #%d: checking agent status", poll_count)
        agents = client.check_agent_status(timeout_sec=poll_timeout)
        logger.info("restart_ae_agent poll #%d: %d agent(s) returned", poll_count, len(agents))
        if agent_name:
            agents = [a for a in agents if a.get("agentName", "") == agent_name]
            if agents:
                logger.info(
                    "restart_ae_agent poll #%d: agent=%s state=%s",
                    poll_count,
                    agents[0].get("agentName", "Unknown"),
                    agents[0].get("agentState", "UNKNOWN"),
                )
        healthy = next(
            (a for a in agents if str(a.get("agentState", "")).upper() in {"CONNECTED", "RUNNING", "ACTIVE"}),
            None,
        )
        if healthy:
            break
        if agents:
            last_state = str(agents[0].get("agentState", "UNKNOWN"))

    if healthy:
        return {
            "success": True,
            "agent_name": healthy.get("agentName", agent_name or "Unknown"),
            "agent_state": healthy.get("agentState", "UNKNOWN"),
            "message": "Agent restarted and is running.",
            "poll_attempts": poll_count,
            "poll_interval_sec": poll_interval_sec,
        }

    message = "Agent restart was triggered, but the agent is still not running."
    if timed_out:
        message = "Agent start command timed out and the agent is still not running."
    payload = {
        "success": False,
        "agent_name": agent_name or "",
        "agent_state": last_state,
        "message": message,
        "poll_attempts": poll_count,
        "poll_interval_sec": poll_interval_sec,
    }
    log_tail = _collect_agent_log_tail()
    if log_tail:
        payload["log_tail"] = log_tail
    if stdout:
        payload["stdout"] = stdout
    if stderr:
        payload["stderr"] = stderr
    return payload


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
    # Identify specific required parameters from the local catalog
    client = get_ae_client()
    schema = client.get_cached_workflow_parameters(workflow_name)
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
        workflow_id = client.get_cached_workflow_id(workflow_name)
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

        # Pending / queued / new / timeout / no_agent path.
        # If still pending, check agent health and provide natural guidance.
        agent_data = client.check_agent_status()
        healthy_agent = next(
            (a for a in agent_data if str(a.get("agentState", "")).upper() in {"CONNECTED", "RUNNING", "ACTIVE"}),
            None,
        )

        if status_upper in {"NO_AGENT"} or not healthy_agent:
            pending_msg = (
                "The request is still pending and no active automation agent is available right now. "
                + ("You can restart the agent with restart_ae_agent. " if str(CONFIG.get("AGENT_STARTUP_PATH", "")).strip() else "")
                + "Please contact your administrator if the agent does not start."
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


tool_registry.register(
    ToolDefinition(
        name="restart_ae_agent",
        description=(
            "Start the AutomationEdge agent process using the local startup path "
            "and wait until it reports RUNNING/CONNECTED."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "agent_name": {
                "type": "string",
                "description": "Optional agent name to verify (empty for any)"
            },
            "poll_interval_sec": {
                "type": "integer",
                "description": "Seconds between status checks (default 2)"
            },
            "max_poll_attempts": {
                "type": "integer",
                "description": "Max poll attempts before giving up (default 15)"
            },
        },
        required_params=[],
    ),
    restart_ae_agent,
)
