"""
P0 agent_read (9) + agent_mutate (2) tools — 11 total.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.agent")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ═══════════════════════════════════════════════════════════════════════
#  agent_read
# ═══════════════════════════════════════════════════════════════════════

async def agent_list_stopped() -> str:
    """List all agents in Stopped state."""
    agents = get_ae_client().list_agents()
    stopped = [
        a for a in agents
        if (a.get("agentState") or a.get("state") or "").upper() in ("STOPPED", "DISCONNECTED", "OFFLINE")
    ]
    items = []
    for a in stopped:
        items.append({
            "agent_id": a.get("agentId") or a.get("id"),
            "agent_name": a.get("agentName") or a.get("name"),
            "state": a.get("agentState") or a.get("state"),
            "last_seen": a.get("lastSeen") or a.get("lastHeartbeat"),
            "controller": a.get("controllerName") or a.get("controller"),
        })
    return _safe_json({"stopped_agents": items, "count": len(items)})


async def agent_list_unknown() -> str:
    """List agents in unknown or unrecognized state."""
    agents = get_ae_client().list_agents()
    known_states = {"CONNECTED", "RUNNING", "ACTIVE", "STOPPED", "DISCONNECTED", "OFFLINE", "IDLE"}
    unknown = [
        a for a in agents
        if (a.get("agentState") or a.get("state") or "UNKNOWN").upper() not in known_states
    ]
    items = []
    for a in unknown:
        items.append({
            "agent_id": a.get("agentId") or a.get("id"),
            "agent_name": a.get("agentName") or a.get("name"),
            "state": a.get("agentState") or a.get("state"),
            "last_seen": a.get("lastSeen") or a.get("lastHeartbeat"),
        })
    return _safe_json({"unknown_agents": items, "count": len(items)})


async def agent_get_status(agent_id: str) -> str:
    """Get the current status of a specific agent."""
    agents = get_ae_client().list_agents()
    match = None
    for a in agents:
        aid = a.get("agentId") or a.get("id") or ""
        aname = a.get("agentName") or a.get("name") or ""
        if str(aid) == agent_id or aname == agent_id:
            logger.info("Matched agent %s (ID: %s) by name or ID", aname, aid)
            match = a
            break
    
    logger.info("Search loop finished. match=%s", match is not None)

    if not match:
        try:
            match = get_ae_client().get_agent(agent_id)
        except Exception:
            return _safe_json({"error": f"Agent '{agent_id}' not found"})

    state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
    return _safe_json({
        "agent_id": match.get("agentId") or match.get("id"),
        "agent_name": match.get("agentName") or match.get("name"),
        "state": state,
        "is_healthy": state in ("CONNECTED", "RUNNING", "ACTIVE"),
        "last_seen": match.get("lastSeen") or match.get("lastHeartbeat"),
        "version": match.get("version") or match.get("agentVersion"),
        "os": match.get("os") or match.get("operatingSystem"),
    })


async def agent_get_details(agent_id: str) -> str:
    """Get full details of an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            {"error": f"Agent '{agent_id}' not found"},
        )
    return _safe_json(data)


async def agent_get_current_load(agent_id: str) -> str:
    """Get the active workload summary for an agent."""
    agents = get_ae_client().list_agents()
    match = None
    for a in agents:
        if (a.get("agentId") or a.get("id") or "") == agent_id or \
           (a.get("agentName") or a.get("name") or "") == agent_id:
            match = a
            break

    running_count = 0
    try:
        running = get_ae_client().get_agent_requests(agent_id)
        running_count = len(running)
    except Exception:
        pass

    if match:
        return _safe_json({
            "agent_id": agent_id,
            "agent_name": match.get("agentName") or match.get("name"),
            "state": match.get("agentState") or match.get("state"),
            "running_requests": running_count,
            "max_concurrent": match.get("maxConcurrent") or match.get("concurrency"),
            "cpu_usage": match.get("cpuUsage"),
            "memory_usage": match.get("memoryUsage"),
        })
    return _safe_json({"agent_id": agent_id, "running_requests": running_count})


async def agent_get_running_requests(agent_id: str, limit: int = 50) -> str:
    """Get requests currently executing on an agent."""
    try:
        requests = get_ae_client().get_agent_requests(agent_id, limit=limit)
    except Exception:
        all_requests = get_ae_client().search_requests(
            filters={"agentName": agent_id, "status": "Running"}, limit=limit
        )
        requests = all_requests

    items = []
    for r in requests[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "status": r.get("status"),
            "created": r.get("createdDate"),
            "user": r.get("userId"),
        })
    return _safe_json({"agent_id": agent_id, "running_requests": items, "count": len(items)})


async def agent_get_assigned_workflows(agent_id: str) -> str:
    """Get workflows assigned to an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
        workflows = data.get("assignedWorkflows") or data.get("workflows") or []
    except Exception:
        workflows = []

    items = []
    for w in workflows:
        if isinstance(w, dict):
            items.append({
                "workflow_id": w.get("workflowId") or w.get("id"),
                "workflow_name": w.get("workflowName") or w.get("name"),
                "active": w.get("active"),
            })
        elif isinstance(w, str):
            items.append({"workflow_name": w})

    return _safe_json({"agent_id": agent_id, "assigned_workflows": items, "count": len(items)})


async def agent_get_connectivity_state(agent_id: str) -> str:
    """Check agent connectivity to controller and platform."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            {},
        )

    state = (data.get("agentState") or data.get("state") or "UNKNOWN").upper()
    return _safe_json({
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "state": state,
        "connected_to_controller": state in ("CONNECTED", "RUNNING", "ACTIVE"),
        "controller": data.get("controllerName") or data.get("controller"),
        "controller_state": data.get("controllerState"),
        "platform_reachable": data.get("platformReachable"),
        "last_heartbeat": data.get("lastSeen") or data.get("lastHeartbeat"),
    })


async def agent_get_rdp_session_state(agent_id: str) -> str:
    """Check RDP/desktop session state for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            {},
        )

    return _safe_json({
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "rdp_session_active": data.get("rdpSessionActive") or data.get("desktopSessionActive"),
        "rdp_user": data.get("rdpUser") or data.get("desktopUser"),
        "screen_resolution": data.get("screenResolution"),
        "locked": data.get("screenLocked") or data.get("isLocked"),
    })


# ═══════════════════════════════════════════════════════════════════════
#  agent_mutate
# ═══════════════════════════════════════════════════════════════════════

async def agent_restart_service(
    agent_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Restart the AE agent service. Privileged operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "restart_agent_service",
            "agent_id": agent_id,
            "reason": reason,
            "message": f"Would restart agent service for {agent_id}. No changes made.",
        })
    data = get_ae_client().restart_agent(agent_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "restart_agent_service",
        "agent_id": agent_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


async def agent_clear_stale_rdp_session(
    agent_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Clear a stuck RDP/desktop session on an agent. Privileged operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "clear_stale_rdp_session",
            "agent_id": agent_id,
            "reason": reason,
            "message": f"Would clear RDP session on agent {agent_id}. No changes made.",
        })
    data = get_ae_client().clear_agent_rdp(agent_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "clear_stale_rdp_session",
        "agent_id": agent_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ═══════════════════════════════════════════════════════════════════════
#  P1 support: list_running, get_recent_failures, get_last_heartbeat, collect_diagnostics
# ═══════════════════════════════════════════════════════════════════════

async def agent_list_running() -> str:
    """List all agents currently in Running/Connected state."""
    agents = get_ae_client().list_agents()
    running = [
        a for a in agents
        if (a.get("agentState") or a.get("state") or "").upper() in ("CONNECTED", "RUNNING", "ACTIVE")
    ]
    items = [{"agent_id": a.get("agentId") or a.get("id"), "agent_name": a.get("agentName") or a.get("name"), "state": a.get("agentState") or a.get("state")} for a in running]
    return _safe_json({"running_agents": items, "count": len(items)})


async def agent_get_recent_failures(agent_id: str, limit: int = 20) -> str:
    """Recent failed requests on this agent."""
    client = get_ae_client()
    failures = client.search_requests(filters={"agentName": agent_id, "status": "Failure"}, limit=limit)
    items = [{"request_id": r.get("id") or r.get("automationRequestId"), "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"), "error": r.get("errorMessage"), "created": r.get("createdDate")} for r in failures[:limit]]
    return _safe_json({"agent_id": agent_id, "recent_failures": items, "count": len(items)})


async def agent_get_last_heartbeat(agent_id: str) -> str:
    """Last seen / last heartbeat time for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next((a for a in agents if (a.get("agentId") or a.get("id")) == agent_id or (a.get("agentName") or a.get("name")) == agent_id), {})
    last = data.get("lastSeen") or data.get("lastHeartbeat") or data.get("lastUpdated")
    return _safe_json({"agent_id": agent_id, "agent_name": data.get("agentName") or data.get("name"), "last_heartbeat": last})


async def agent_collect_diagnostics(agent_id: str) -> str:
    """Gather support diagnostics for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        data = {}
    agents = get_ae_client().list_agents()
    match = next((a for a in agents if (a.get("agentId") or a.get("id")) == agent_id or (a.get("agentName") or a.get("name")) == agent_id), data)
    return _safe_json({"agent_id": agent_id, "diagnostics": match})
