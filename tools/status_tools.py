"""
Status & health monitoring tools.
"""

import logging
from datetime import datetime, timedelta, timezone

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.status")


def check_workflow_status(workflow_name: str = "", status: str = "") -> dict:
    """Check the status of bots/workflows, including a 24h summary and filtering.
    
    If workflow_name is provided, checks that specific bot using semantic resolution if needed.
    If workflow_name is empty, provides a global summary for all bots.
    """
    client = get_ae_client()
    status_filter = str(status or "").strip()
    query_name = str(workflow_name or "").strip()
    
    # 1. Detect if query_name is a numeric Request ID
    if query_name.isdigit() and len(query_name) > 4:  # Likely a request ID, not a short workflow ID
        try:
            instance = client.get_workflow_instance_by_id(query_name)
            if instance:
                return _format_single_instance_response(instance)
        except Exception as exc:
            logger.warning(f"Direct request ID lookup failed for {query_name}: {exc}")

    # 2. Resolve technical name
    resolved_name = ""
    if query_name:
        # Try local cache first (exact/WF_ prefix variants)
        resolved_name = client.resolve_cached_workflow_name(query_name)
        
        # If not found, try RAG/Semantic resolution for fuzzy/partial names
        if not resolved_name:
            logger.info(f"Fuzzy match not found for '{query_name}', trying RAG resolution...")
            resolved_name = client.resolve_workflow_via_rag(query_name)
            
    name_to_check = resolved_name or query_name
    
    try:
        # Increase limit to 300 to find older executions
        instances = client.get_workflow_instances(
            name_to_check, 
            limit=300,
            status_filter=status_filter if status_filter else None
        )
    except Exception as exc:
        msg = f"Failed to fetch status for '{name_to_check or 'All Bots'}': {exc}"
        logger.warning(msg)
        return {
            "workflow_name": name_to_check or "All Bots",
            "status": "UNKNOWN",
            "error_message": str(exc),
            "message": msg
        }

    if not instances:
        msg = f"No recent executions found"
        if name_to_check:
            msg += f" for '{name_to_check}'"
        if status_filter:
            msg += f" with status '{status_filter}'"
        return {
            "workflow_name": name_to_check or "All Bots",
            "status": "NO_EXECUTIONS",
            "message": msg,
        }

    # Latest instance details - ALWAYS present regardless of 24h
    latest = instances[0]
    latest_ts = _parse_timestamp(latest.get("createdDate") or latest.get("started_at"))
    
    # 24-hour summary logic
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    summary = {
        "New": 0, "InProgress": 0, "Complete": 0, "Failure": 0, 
        "ExecutionStarted": 0, "Retry": 0, "Expired": 0, "Diverted": 0, 
        "Terminated": 0, "Cancelled": 0, "Resubmitted": 0, "Awaitinginput": 0
    }
    total_24h = 0
    
    recent_list = []
    for item in instances:
        ts = _parse_timestamp(
            item.get("createdDate") or item.get("started_at")
        )
        item_status = item.get("status", "Unknown")
        # Keep history of recent ones as requested by the user
        if len(recent_list) < 50:
            recent_list.append({
                "id": item.get("id") or item.get("automationRequestId"),
                "bot_name": item.get("workflowName") or (item.get("workflowConfiguration") or {}).get("name") or "Unknown Bot",
                "status": item_status,
                "time": str(ts) if ts else "Unknown",
                "agent": item.get("agentName")
            })

        if ts and ts >= cutoff:
            total_24h += 1
            if item_status in summary:
                summary[item_status] += 1
            else:
                summary[item_status] = summary.get(item_status, 0) + 1

    active_summary = {k: v for k, v in summary.items() if v > 0}

    # Resolve display name
    display_name = name_to_check if name_to_check else "All Bots"
    if latest:
        display_name = (
            latest.get("workflowName") or 
            (latest.get("workflowConfiguration") or {}).get("name") or 
            display_name
        )

    # User message construction
    if query_name:
        latest_status = latest.get("status")
        time_str = f"on {latest_ts.strftime('%Y-%m-%d %H:%M:%S UTC')}" if latest_ts else "recently"
        msg = f"The absolute latest execution for bot '**{display_name}**' was {time_str} and its status is '**{latest_status}**'."
        if latest_ts and latest_ts < cutoff:
            msg += f" (Note: This run is older than 24 hours)."
        msg += f" You can use execution ID `{latest.get('id') or latest.get('automationRequestId')}` to fetch logs if needed."
    else:
        msg = f"Global status summary for all bots (Last 24 hours)."

    return {
        "bot_name": display_name,
        "workflow_name": display_name,
        "is_global_check": not bool(query_name),
        "latest_status": latest.get("status"),
        "latest_execution": {
            "id": latest.get("id") or latest.get("automationRequestId"),
            "bot_name": latest.get("workflowName") or (latest.get("workflowConfiguration") or {}).get("name") or "Unknown Bot",
            "status": latest.get("status"),
            "timestamp": str(latest_ts) if latest_ts else "Unknown"
        },
        "latest_execution_id": latest.get("id") or latest.get("automationRequestId"),
        "last_24h_summary": active_summary,
        "total_executions_24h": total_24h,
        "recent_executions": recent_list,
        "status_filter_applied": status_filter or "None",
        "message": msg
    }


def _format_single_instance_response(instance: dict) -> dict:
    """Helper to format a single T4 instance into the standard status response."""
    ts = _parse_timestamp(instance.get("createdDate") or instance.get("started_at"))
    status = instance.get("status", "Unknown")
    bot_name = (
        instance.get("workflowName") or 
        (instance.get("workflowConfiguration") or {}).get("name") or 
        "Unknown Bot"
    )
    request_id = instance.get("id") or instance.get("automationRequestId")
    
    time_str = f"on {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}" if ts else "recently"
    # Ensure request_id is not empty
    rid_str = f"`{request_id}`" if request_id else "unknown"
    msg = f"Execution ID {rid_str} for bot '**{bot_name}**' was found. Its current status is '**{status}**' ({time_str})."
    
    return {
        "bot_name": bot_name,
        "workflow_name": bot_name,
        "status": status,
        "latest_status": status,
        "latest_execution": {
            "id": request_id,
            "bot_name": bot_name,
            "status": status,
            "timestamp": str(ts) if ts else "Unknown"
        },
        "latest_execution_id": request_id,
        "message": msg,
        "is_single_search": True
    }


def _parse_timestamp(value):
    if value is None:
        return None
    try:
        val = float(value)
        # Handle milliseconds (13 digits) vs seconds
        if val > 10000000000:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass

    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def list_recent_failures(
    hours: int = 24,
    limit: int = 300,  # Increased default for deep search
    workflow_name: str = "",
) -> dict:
    client = get_ae_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))
    query_name = str(workflow_name or "").strip()

    # 1. Resolve technical name if query_name provided
    resolved_name = ""
    if query_name:
        resolved_name = client.resolve_cached_workflow_name(query_name)
        if not resolved_name:
            logger.info(f"Fuzzy match not found for '{query_name}' in failures check, trying RAG...")
            resolved_name = client.resolve_workflow_via_rag(query_name)
            
    name_to_check = resolved_name or query_name
    data = []
    last_error = ""

    if name_to_check:
        try:
            # Benefiting from the new paging logic in AutomationEdgeClient
            data = client.get_workflow_instances(
                name_to_check,
                limit=limit,
                status_filter="Failure"
            )
            last_error = ""
        except Exception as exc:
            last_error = str(exc)

    if not data:
        # 2. Try global failures modern API if no specific name or previous search failed
        if not name_to_check:
            try:
                resp = client.request(
                    "GET",
                    "/api/v1/failures/recent",
                    use_rest_prefix=False,
                    silent_on_status=[400, 403, 404],
                )
                if isinstance(resp, dict):
                    data = resp.get("failures") or resp.get("executions") or resp.get("data") or []
                elif isinstance(resp, list):
                    data = resp
                last_error = ""
            except Exception as exc:
                last_error = str(exc)
        
        # 3. Use paging-aware global T4 check if still no data
        if not data:
            try:
                # get_workflow_instances("") handles global listing with paging
                data = client.get_workflow_instances(
                    workflow_name="",
                    limit=limit,
                    status_filter="Failure"
                )
                last_error = ""
            except Exception as exc:
                if not last_error:
                    last_error = str(exc)

    if not isinstance(data, list):
        data = []

    failures = []
    for item in data:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).upper()
        if status not in {"FAILURE", "ERROR", "FAILED"}:
            continue

        ts = _parse_timestamp(
            item.get("createdDate")
            or item.get("lastUpdatedDate")
            or item.get("completedDate")
            or item.get("started_at")
            or item.get("completed_at")
        )
        if ts and ts < cutoff:
            continue

        failures.append(
            {
                "execution_id": item.get("id") or item.get("automationRequestId") or item.get("execution_id"),
                "workflow_name": item.get("workflowName")
                or item.get("workflow_name")
                or ((item.get("workflowConfiguration") or {}).get("name")),
                "status": item.get("status"),
                "agent_name": item.get("agentName"),
                "error_message": item.get("errorMessage") or item.get("errorDetails") or item.get("error"),
                "created_date": item.get("createdDate") or item.get("started_at"),
                "completed_date": item.get("completedDate") or item.get("completed_at"),
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
    client = get_ae_client()
    org = str(client.default_org_code or "").strip()
    candidates = []
    if org:
        candidates.append((f"/tenants/{org}/system/health", False))
        candidates.append((f"/{org}/system/health", False))
    candidates.extend(
        [
            ("/api/v1/system/health", False),
            ("/system/health", False),
        ]
    )
    if org:
        candidates.append((f"/tenants/{org}/system/health", True))
        candidates.append((f"/{org}/system/health", True))
    candidates.append(("/system/health", True))

    last_error = None
    resp = None
    for path, use_rest_prefix in candidates:
        try:
            resp = client.request(
                "GET", path, use_rest_prefix=use_rest_prefix, silent_on_status=[400, 403, 404]
            )
            break
        except Exception as exc:
            last_error = exc

    if resp is None:
        # Fallback for common 403 Forbidden errors if platform-level health is restricted
        try:
            agents_info = get_agent_status()
            if agents_info.get("success"):
                agents = agents_info.get("agents", [])
                online = sum(1 for a in agents if a.get("agentState") in ("CONNECTED", "RUNNING"))
                return {
                    "status": "limited_health_info",
                    "agents_online": online,
                    "agents_offline": len(agents) - online,
                    "agents": agents,
                    "warning": "Primary system health API returned 403 Forbidden; showing basic agent monitoring status instead.",
                }
        except Exception:
            pass
        raise last_error or RuntimeError("Could not fetch system health")

    if resp is None or not isinstance(resp, dict):
        agents = []
        online = 0
    else:
        agents = resp.get("agents", [])
        online = sum(1 for a in agents if a.get("status") == "online")

    return {
        "status": (resp or {}).get("status", "unknown"),
        "agents_online": online,
        "agents_offline": len(agents) - online,
        "agents": agents,
        "queue_depth": resp.get("queue_depth", 0),
        "active_executions": resp.get("active_executions", 0),
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
    # Resolve the name first for accurate schema lookup
    resolved_name = client.resolve_cached_workflow_name(workflow_name) or workflow_name
    
    # AE-77: Check for "File" type parameters. File upload is not supported in agentic chat yet.
    schema = client.get_cached_workflow_parameters(resolved_name)
    file_params = [
        p.get("name") for p in schema 
        if str(p.get("type", "")).strip().lower() in {"file", "attachment", "upload"}
    ]
    if file_params:
        logger.info(f"Workflow '{resolved_name}' requires file upload. Rejecting agentic trigger in t4_execute_and_poll.")
        return {
            "success": False,
            "error": (
                f"I've identified that the **{resolved_name}** bot requires a **document upload** "
                f"for the following parameter(s): `{', '.join(file_params)}`. \n\n"
                "This action cannot be completed through the chat yet. "
                "Please go to the **AutomationEdge (AE) server** to trigger this bot manually. "
                "Thank you!"
            ),
            "reason": f"Workflow requires file upload for: {', '.join(file_params)}",
            "workflow_name": resolved_name
        }

    required = client.get_required_parameters(resolved_name)
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
            workflow_name=resolved_name,
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
        "in_progress": poll_result.get(
            "in_progress_hint",
            f"Execution still running. Use request_id {request_id} to check status.",
        ),
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
    
    Use this when you have a numeric request_id or execution_id.
    Returns status, bot name, agent, timings, and any error message.
    """
    resp = get_ae_client().get_execution_status(execution_id)
    status = resp.get("status", "UNKNOWN")
    
    # Enrich the response for LLM decision making
    return {
        "execution_id": execution_id,
        "status": status,
        "workflow_name": resp.get("workflowName") or resp.get("workflow_name") or (resp.get("workflowConfiguration") or {}).get("name"),
        "agent_name": resp.get("agentName"),
        "start_time": resp.get("startTime") or resp.get("createdDate"),
        "end_time": resp.get("endTime") or resp.get("lastUpdatedDate"),
        "error_message": resp.get("errorMessage") or resp.get("errorDetails") or resp.get("workflowResponse"),
        "raw": resp,
        "recommendation": f"Use 'get_execution_logs' with execution_id '{execution_id}' to see technical details/errors." if status in ("Failure", "Error", "Complete") else "Execution is still in progress."
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
            "Get detailed status of a specific workflow execution or numeric request ID (e.g. 2501865). "
            "Checks both global and org-scoped T4 workflowinstances endpoints. "
            "Use this when the user provides a specific investigation target by ID."
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
            "Check the CURRENT, HISTORICAL, or EXECUTION status/records of bots (workflows). "
            "Returns the absolute latest execution details (even if old), request ID, "
            "and a 24-hour summary. Use this to find out 'did my bot run', 'is it failing', "
            "'what was the last status', 'what is the execution status', or 'how many times did it run today'."
        ),
        category="status",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Name of the workflow/bot to check (e.g. 'Maturity Claim' or 'Email Bot JD'). Handles natural language names.",
            },
            "status": {
                "type": "string",
                "description": "Optional: Filter history by status (e.g., 'Complete', 'Failure', 'New', 'InProgress')",
            },
        },
        required_params=[],
        always_available=True,
        use_when="The user asks about the status, history, last run, or current state of any bot or workflow.",
        avoid_when="The user explicitly wants to EXECUTE, RUN, or TRIGGER a new workflow instance.",
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



def list_workflows(limit: int = 100) -> dict:
    """List all available AutomationEdge workflows."""
    try:
        from tools.base import get_ae_client
        client = get_ae_client()
        workflows = client.list_workflows(page_size=limit)
        items = []
        for w in workflows:
            items.append({
                "workflow_id": w.get("workflowId") or w.get("id"),
                "workflow_name": w.get("workflowName") or w.get("name"),
                "description": w.get("description"),
                "active": w.get("active", True),
            })
        return {"workflows": items, "count": len(items)}
    except Exception as exc:
        logger.error("list_workflows failed: %s", exc)
        return {"error": str(exc), "workflows": [], "count": 0}


tool_registry.register(
    ToolDefinition(
        name="ae.workflow.list",
        description="List all available AutomationEdge workflows.",
        category="dependency",
        tier="read_only",
        parameters={
            "limit": {
                "type": "integer",
                "description": "Max number of workflows to return",
                "default": 100,
            }
        },
        required_params=[],
    ),
    list_workflows,
)
