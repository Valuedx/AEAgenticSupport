"""
Status & health monitoring tools.
"""

import logging

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.status")


def check_workflow_status(workflow_name: str) -> dict:
    resp = get_ae_client().get(f"/api/workflows/{workflow_name}/status")
    return {
        "workflow_name": workflow_name,
        "status": resp.get("status", "UNKNOWN"),
        "last_run_time": resp.get("lastRunTime"),
        "last_duration_seconds": resp.get("lastDuration"),
        "error_message": resp.get("errorMessage"),
        "next_scheduled_run": resp.get("nextScheduledRun"),
    }


def list_recent_failures(hours: int = 24, limit: int = 20) -> dict:
    resp = get_ae_client().get(
        "/api/executions/failures",
        params={"hours": hours, "limit": limit},
    )
    return {
        "failures": resp.get("failures", []),
        "total_count": len(resp.get("failures", [])),
        "time_window_hours": hours,
    }


def get_system_health() -> dict:
    resp = get_ae_client().get("/api/system/health")
    return {
        "agents_online": resp.get("agentsOnline", 0),
        "agents_offline": resp.get("agentsOffline", 0),
        "total_queues": resp.get("totalQueues", 0),
        "stuck_items": resp.get("stuckItems", 0),
        "scheduled_workflows": resp.get("scheduledWorkflows", 0),
        "disabled_workflows": resp.get("disabledWorkflows", 0),
    }


def get_queue_status(queue_name: str) -> dict:
    resp = get_ae_client().get(f"/api/queues/{queue_name}/status")
    return {
        "queue_name": queue_name,
        "depth": resp.get("depth", 0),
        "processing_rate": resp.get("processingRate"),
        "stuck_items": resp.get("stuckItems", 0),
        "oldest_item_age_seconds": resp.get("oldestItemAge"),
    }


def get_agent_status(agent_name: str = "") -> dict:
    if agent_name:
        return get_ae_client().get(f"/api/agents/{agent_name}/status")
    return get_ae_client().get("/api/agents/status")


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
