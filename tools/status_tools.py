"""
Status & health monitoring tools.
"""

import logging
from datetime import datetime, timedelta, timezone

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.status")


def check_workflow_status(workflow_name: str) -> dict:
    # Resolve endpoint variations via client fallback (prefix + org/global paths).
    try:
        data = get_ae_client().get_workflow_latest_instance(workflow_name)
    except Exception as exc:
        logger.warning("check_workflow_status fallback failed for %s: %s", workflow_name, exc)
        return {
            "workflow_name": workflow_name,
            "status": "UNKNOWN",
            "last_execution_status": None,
            "request_id": None,
            "agent": None,
            "error_message": str(exc),
        }

    return {
        "workflow_name": workflow_name,
        "status": data.get("status", "UNKNOWN"),
        "last_execution_status": data.get("status"),
        "request_id": data.get("automationRequestId") or data.get("id"),
        "agent": data.get("agentName"),
        "error_message": data.get("errorMessage") or data.get("errorDetails"),
    }


def list_recent_failures(hours: int = 24, limit: int = 20) -> dict:
    client = get_ae_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))

    # T4-compatible fallbacks for workflow instances listing.
    candidates = [
        ("POST", "/workflowinstances", True),
        ("POST", f"/{client.default_org_code}/workflowinstances", True),
        ("GET", "/workflowinstances", True),
        ("GET", f"/{client.default_org_code}/workflowinstances", True),
        ("POST", "/workflowinstances", False),
        ("GET", "/workflowinstances", False),
    ]

    data = []
    last_error = ""
    for method, path, use_rest_prefix in candidates:
        try:
            resp = client.request(
                method,
                path,
                params={"offset": 0, "size": max(limit * 3, 20), "order": "desc"},
                use_rest_prefix=use_rest_prefix,
            )
            if isinstance(resp, dict):
                data = resp.get("data") or resp.get("instances") or resp.get("executions") or []
            elif isinstance(resp, list):
                data = resp
            if isinstance(data, list):
                break
        except Exception as exc:
            last_error = str(exc)
            continue

    if not isinstance(data, list):
        data = []

    failures = []
    for item in data:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).upper()
        if status not in {"FAILURE", "ERROR", "FAILED"}:
            continue

        ts_ms = item.get("createdDate") or item.get("lastUpdatedDate") or item.get("completedDate")
        ts = None
        if isinstance(ts_ms, (int, float)):
            try:
                ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            except Exception:
                ts = None
        if ts and ts < cutoff:
            continue

        failures.append(
            {
                "execution_id": item.get("id") or item.get("automationRequestId"),
                "workflow_name": item.get("workflowName")
                or ((item.get("workflowConfiguration") or {}).get("name")),
                "status": item.get("status"),
                "agent_name": item.get("agentName"),
                "error_message": item.get("errorMessage") or item.get("errorDetails"),
                "created_date": item.get("createdDate"),
                "completed_date": item.get("completedDate"),
            }
        )
        if len(failures) >= limit:
            break

    result = {
        "failures": failures,
        "total_count": len(failures),
        "time_window_hours": hours,
    }
    if not failures and last_error:
        result["warning"] = f"No recent failures found. Last endpoint error: {last_error}"
    return result


def get_system_health() -> dict:
    """Check system health via agent monitoring."""
    client = get_ae_client()
    try:
        agents = client.check_agent_status()
        online = sum(1 for a in agents if a.get("agentState") == "Online")
        
        # Try to get queue depth if possible, otherwise default to 0
        queue_depth = 0
        try:
            # Re-using the list_recent_failures logic for a quick queue check if needed,
            # but for health we primarily care about agents.
            pass
        except Exception:
            pass

        return {
            "status": "online" if online > 0 else "degraded",
            "agents_online": online,
            "agents_offline": len(agents) - online,
            "agents": agents[:10], # Limit to 10 for brevity
            "queue_depth": queue_depth,
            "active_executions": 0, # T4 doesn't expose this at root easily
        }
    except Exception as exc:
        logger.warning("get_system_health failed: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "agents_online": 0,
            "agents_offline": 0,
        }

def get_queue_status(queue_name: str) -> dict:
    org = get_ae_client().default_org_code
    resp = get_ae_client().get(f"/{org}/queues/{queue_name}/status")
    return {
        "queue_name": queue_name,
        "pending": resp.get("pending", 0),
        "running": resp.get("running", 0),
        "completed_today": resp.get("completed_today", 0),
        "failed_today": resp.get("failed_today", 0),
    }


def get_agent_status(agent_name: str = "") -> dict:
    """Check T4 agent health via POST /monitoring/agents.

    Ref: code_ref.py t4_check_agent_status() / t4_get_agent_monitoring()
    """
    client = get_ae_client()
    agents = client.check_agent_status()

    if not agents:
        return {
            "success": False,
            "agents": [],
            "total": 0,
            "message": "No agents found or monitoring endpoint unreachable.",
        }

    if agent_name:
        agents = [a for a in agents if a.get("agentName", "") == agent_name]

    selected = next(
        (a for a in agents if a.get("agentState", "").upper() in ("CONNECTED", "RUNNING")),
        agents[0] if agents else {},
    )

    return {
        "success": True,
        "agents": agents,
        "total": len(agents),
        "selected_agent": {
            "name": selected.get("agentName", "Unknown"),
            "id": selected.get("agentId") or selected.get("id"),
            "state": selected.get("agentState", "UNKNOWN"),
        },
    }


def t4_check_agent_status_tool(agent_name: str = "") -> dict:
    """T4 Status Check Agent — mirrors code_ref.py t4_get_agent_details().

    Uses POST /monitoring/agents endpoint and selects the best available agent.
    """
    client = get_ae_client()
    agents = client.check_agent_status()

    if not agents:
        return {
            "success": False,
            "agents": [],
            "state": "NO_AGENTS",
            "message": "No agents returned. Check T4_ORG_CODE and T4 connectivity.",
        }

    if agent_name:
        named = [a for a in agents if a.get("agentName") == agent_name]
        if named:
            agents = named

    selected = next(
        (a for a in agents if a.get("agentState", "").upper() in ("CONNECTED", "RUNNING")),
        agents[0],
    )

    state = selected.get("agentState", "UNKNOWN").upper()
    is_healthy = state in ("CONNECTED", "RUNNING", "ACTIVE")

    return {
        "success": True,
        "agent_name": selected.get("agentName", "Unknown"),
        "agent_id": selected.get("agentId") or selected.get("id"),
        "agent_state": state,
        "is_healthy": is_healthy,
        "all_agents": agents,
        "message": (
            f"Agent '{selected.get('agentName')}' is {state} and healthy."
            if is_healthy
            else f"Agent '{selected.get('agentName')}' is {state} — may need attention."
        ),
    }


def t4_execute_and_poll(
    workflow_name: str,
    workflow_id: str,
    params: dict = None,
    poll_interval_sec: int = 5,
    max_poll_attempts: int = 60,
) -> dict:
    """T4 Execution Agent — execute a workflow and poll until complete.

    Ref: code_ref.py t4_execute_workflow() + t4_poll_status()
    Builds the correct T4 payload: orgCode, workflowName, params list format.
    """
    client = get_ae_client()
    org = client.default_org_code

    # ── "Ask Again" pattern ──
    # Identify specific required parameters from the local catalog
    schema = client.get_cached_workflow_parameters(workflow_name)
    # T4 Catalogue uses 'optional': false for required parameters
    required = [
        p["name"] for p in schema 
        if p.get("required") or p.get("is_required") or p.get("optional") is False
    ]
    missing = [p for p in required if not (params or {}).get(p)]

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
            "tool_name": "t4_execute_and_poll",
            "workflow_name": workflow_name,
            "missing_params": missing
        }

    # Execute via the updated client method (handles payload format + query params automatically)
    try:
        execute_resp = client.execute_workflow(
            workflow_name=workflow_name,
            workflow_id=workflow_id,
            params=params,
            source="ae-agentic-support-status-check"
        )
    except Exception as exc:
        logger.error("T4 execute failed: %s", exc)
        return {"success": False, "error": str(exc)}

    request_id = (
        execute_resp.get("automationRequestId")
        or execute_resp.get("requestId")
        or execute_resp.get("id")
    )
    if not request_id:
        return {
            "success": False,
            "error": "T4 did not return a request/execution ID.",
            "raw": execute_resp,
        }

    logger.info(
        "T4 execute: workflow=%s request_id=%s — polling...", workflow_name, request_id
    )

    poll_result = client.poll_execution_status(
        execution_id=str(request_id),
        poll_interval_sec=poll_interval_sec,
        max_attempts=max_poll_attempts,
    )

    status = poll_result.get("status", "unknown")
    raw = poll_result.get("raw") or {}

    # ── Extract detailed workflowResponse ──
    detailed_msg = ""
    wf_resp_str = raw.get("workflowResponse")
    if wf_resp_str:
        try:
            import json
            wf_resp = json.loads(wf_resp_str)
            detailed_msg = wf_resp.get("message") or ""
        except Exception:
            pass

    status_messages = {
        "Complete": f"'{workflow_name}' completed successfully! {detailed_msg}".strip(),
        "Failure": f"'{workflow_name}' encountered a failure. Check logs for details.",
        "no_agent": "No automation agent was available. Please check agent health.",
        "timeout": "Execution timed out waiting for a result.",
        "Error": f"'{workflow_name}' encountered an error.",
    }

    return {
        "success": status == "Complete",
        "status": status,
        "request_id": str(request_id),
        "workflow_name": workflow_name,
        "message": status_messages.get(status, f"Status: {status}"),
        "raw": raw,
    }


def get_execution_status(execution_id: str) -> dict:
    """Get status of a specific workflow execution by ID.

    Tries both global and org-scoped T4 workflowinstances paths.
    Ref: code_ref.py t4_poll_status() dual-URL logic.
    """
    resp = get_ae_client().get_execution_status(execution_id)
    return {
        "execution_id": execution_id,
        "status": resp.get("status", "UNKNOWN"),
        "workflow_name": resp.get("workflowName") or resp.get("workflow_name"),
        "agent_name": resp.get("agentName"),
        "start_time": resp.get("startTime") or resp.get("createdDate"),
        "end_time": resp.get("endTime"),
        "error_message": resp.get("errorMessage") or resp.get("errorDetails"),
        "raw": resp,
    }


# ── Register tools ──

tool_registry.register(
    ToolDefinition(
        name="t4_execute_and_poll",
        description=(
            "T4 Execution Agent: Execute a specific T4 workflow by name and ID, "
            "then poll until it completes (Complete/Failure/Error). "
            "Returns final status, request ID, and result message. "
            "Use this when the user wants to RUN or TRIGGER an automation workflow."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "The exact T4 workflow name",
            },
            "workflow_id": {
                "type": "string",
                "description": "The numeric T4 workflow ID",
            },
            "params": {
                "type": "object",
                "description": "Key-value dict of workflow input parameters",
            },
            "poll_interval_sec": {
                "type": "integer",
                "description": "Seconds between status polls (default 5)",
            },
            "max_poll_attempts": {
                "type": "integer",
                "description": "Max polls before giving up (default 60)",
            },
        },
        required_params=["workflow_name", "workflow_id"],
        always_available=True,
    ),
    t4_execute_and_poll,
)

tool_registry.register(
    ToolDefinition(
        name="t4_check_agent_status",
        description=(
            "T4 Status Check Agent: Check if a T4 automation agent is RUNNING/CONNECTED "
            "using the POST /monitoring/agents endpoint. "
            "Returns agent name, ID, state, and is_healthy flag. "
            "Use this when the user asks 'is my agent running?' or 'check agent health'."
        ),
        category="status",
        tier="read_only",
        parameters={
            "agent_name": {
                "type": "string",
                "description": "Agent name to check (empty = check first/best agent)",
            },
        },
        required_params=[],
        always_available=True,
    ),
    t4_check_agent_status_tool,
)

tool_registry.register(
    ToolDefinition(
        name="get_execution_status",
        description=(
            "Get detailed status of a specific workflow execution by ID. "
            "Checks both global and org-scoped T4 workflowinstances endpoints. "
            "Use for async tracking after triggering a workflow."
        ),
        category="status",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The automation request ID / execution ID to track",
            },
        },
        required_params=["execution_id"],
        always_available=True,
    ),
    get_execution_status,
)

tool_registry.register(
    ToolDefinition(
        name="check_workflow_status",
        description=(
            "Check the current status of a specific workflow or execution, "
            "including last run time, duration, and any error messages."
        ),
        category="status",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Name of the workflow to check",
            },
        },
        required_params=["workflow_name"],
        always_available=True,
    ),
    check_workflow_status,
)

tool_registry.register(
    ToolDefinition(
        name="list_recent_failures",
        description=(
            "List all failed workflow executions within a specified time "
            "window. Useful for identifying patterns or cascade failures."
        ),
        category="status",
        tier="read_only",
        parameters={
            "hours": {
                "type": "integer",
                "description": "Time window in hours (default 24)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20)",
            },
        },
        required_params=[],
        always_available=True,
    ),
    list_recent_failures,
)

tool_registry.register(
    ToolDefinition(
        name="get_system_health",
        description=(
            "Get overall AutomationEdge platform health: agent counts, "
            "queue totals, stuck items, workflow stats."
        ),
        category="status",
        tier="read_only",
        parameters={},
        required_params=[],
        always_available=True,
    ),
    get_system_health,
)

tool_registry.register(
    ToolDefinition(
        name="get_queue_status",
        description=(
            "Check queue depth, processing rate, and stuck items "
            "for a specific queue."
        ),
        category="status",
        tier="read_only",
        parameters={
            "queue_name": {
                "type": "string",
                "description": "Name of the queue to check",
            },
        },
        required_params=["queue_name"],
    ),
    get_queue_status,
)

tool_registry.register(
    ToolDefinition(
        name="get_agent_status",
        description=(
            "Check if T4 AE agents/bots are online using the T4 monitoring API. "
            "Returns agent state (RUNNING/CONNECTED/STOPPED) for all or a named agent."
        ),
        category="status",
        tier="read_only",
        parameters={
            "agent_name": {
                "type": "string",
                "description": "Agent name (empty for all agents)",
            },
        },
        required_params=[],
    ),
    get_agent_status,
)
