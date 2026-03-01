"""
Status & health monitoring tools.
"""

import logging

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.status")


def check_workflow_status(workflow_name: str) -> dict:
    resp = get_ae_client().get(f"/api/v1/workflows/{workflow_name}/status")
    return {
        "workflow_name": workflow_name,
        "status": resp.get("status", "UNKNOWN"),
        "last_execution_status": resp.get("last_execution_status"),
        "schedule": resp.get("schedule"),
        "agent": resp.get("agent"),
        "error_message": resp.get("errorMessage"),
    }


def list_recent_failures(hours: int = 24, limit: int = 20) -> dict:
    resp = get_ae_client().get(
        "/api/v1/failures/recent",
        params={"hours": hours, "limit": limit},
    )
    failures = resp.get("failures", [])
    return {
        "failures": failures,
        "total_count": resp.get("total", len(failures)),
        "time_window_hours": hours,
    }


def get_system_health() -> dict:
    resp = get_ae_client().get("/api/v1/system/health")
    agents = resp.get("agents", [])
    online = sum(1 for a in agents if a.get("status") == "online")
    return {
        "status": resp.get("status", "unknown"),
        "agents_online": online,
        "agents_offline": len(agents) - online,
        "agents": agents,
        "queue_depth": resp.get("queue_depth", 0),
        "active_executions": resp.get("active_executions", 0),
    }


def get_queue_status(queue_name: str) -> dict:
    resp = get_ae_client().get(
        f"/api/v1/queues/{queue_name}/status",
    )
    return {
        "queue_name": queue_name,
        "pending": resp.get("pending", 0),
        "running": resp.get("running", 0),
        "completed_today": resp.get("completed_today", 0),
        "failed_today": resp.get("failed_today", 0),
    }


def get_agent_status(agent_name: str = "") -> dict:
    if agent_name:
        return get_ae_client().get(f"/api/v1/agents/{agent_name}/status")
    return get_ae_client().get("/api/v1/agents/status")


# ── Register status tools ──

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
            "Check if AE agents/bots are online, their CPU/memory usage, "
            "and current workload."
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
