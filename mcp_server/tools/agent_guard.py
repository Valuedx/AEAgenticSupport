"""
Agent status guard for log access.

Before fetching execution/request logs, verify that the assigned agent
is in a healthy (Running/Connected/Active) state.  If the agent is
Stopped, Disconnected, or Unknown the platform cannot extract logs,
so we return an actionable "restart your agent" message instead.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.agent_guard")

# States where the agent can serve log-extraction requests
_HEALTHY_STATES = {"CONNECTED", "RUNNING", "ACTIVE"}


async def check_agent_for_request(request_id: str) -> Optional[dict[str, Any]]:
    """Pre-flight check: is the agent assigned to *request_id* healthy?

    Returns
    -------
    None
        Agent is healthy (or no agent is assigned) — caller should proceed.
    dict
        Agent is offline / unknown.  The dict is a ready-to-return JSON
        payload with ``agent_offline = True`` and a user-friendly message.
    """
    client = get_ae_client()

    # 1. Fetch the request to learn which agent & workflow it belongs to
    try:
        request_data = client.get_request(request_id)
    except Exception:
        # Request not found — let the downstream tool handle the 404
        logger.debug("Could not fetch request %s for agent guard", request_id)
        return None

    agent_name = (
        request_data.get("agentName")
        or request_data.get("agentId")
        or ""
    )
    workflow_name = (
        request_data.get("workflowName")
        or (request_data.get("workflowConfiguration") or {}).get("name")
        or ""
    )

    if not agent_name:
        # No agent assigned — nothing to guard
        return None

    # 2. Look up the agent's current state
    try:
        agents = client.list_agents()
    except Exception:
        logger.warning("Could not list agents for guard check")
        return None

    match = None
    for a in agents:
        aid = str(a.get("agentId") or a.get("id") or "")
        aname = str(a.get("agentName") or a.get("name") or "")
        if aid == agent_name or aname.lower() == agent_name.lower():
            match = a
            break

    if match is None:
        # Agent not found in the platform list — let downstream handle it
        return None

    state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
    resolved_name = match.get("agentName") or match.get("name") or agent_name

    if state in _HEALTHY_STATES:
        return None  # ✅ Agent is healthy — proceed

    # ❌ Agent is NOT running
    logger.info(
        "Agent guard blocked log access: agent=%s state=%s workflow=%s request=%s",
        resolved_name, state, workflow_name, request_id,
    )
    return {
        "agent_offline": True,
        "error": (
            f"Cannot retrieve logs — the agent '{resolved_name}' is currently "
            f"{state}. Please restart the agent first and try again."
        ),
        "message": (
            f"⚠️ Your agent **{resolved_name}** is currently **{state}** "
            f"for workflow **{workflow_name or 'N/A'}**.\n\n"
            f"Logs can only be extracted when the assigned agent is Running. "
            f"Please restart the agent and then request the logs again."
        ),
        "agent_name": resolved_name,
        "agent_state": state,
        "workflow_name": workflow_name,
        "request_id": request_id,
    }
