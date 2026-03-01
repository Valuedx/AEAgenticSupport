"""
Dependency analysis tools — trace upstream/downstream workflow
relationships, configuration, and scheduling.
"""

import logging

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.dependency")


def get_workflow_dependencies(workflow_name: str) -> dict:
    resp = get_ae_client().get(f"/api/v1/workflows/{workflow_name}/dependencies")
    return {
        "workflow_name": workflow_name,
        "upstream": resp.get("upstream", []),
        "downstream": resp.get("downstream", []),
        "shared_resources": resp.get("sharedResources", []),
    }


def get_workflow_config(workflow_name: str) -> dict:
    resp = get_ae_client().get(f"/api/v1/workflows/{workflow_name}/config")
    return {
        "workflow_name": workflow_name,
        "input_paths": resp.get("input_paths", []),
        "output_paths": resp.get("output_paths", []),
        "timeout_minutes": resp.get("timeout_minutes"),
        "retry_count": resp.get("retry_count"),
        "parameters": resp.get("parameters", {}),
    }


def get_schedule_info(workflow_name: str) -> dict:
    resp = get_ae_client().get(f"/api/v1/workflows/{workflow_name}/schedule")
    return {
        "workflow_name": workflow_name,
        "cron_expression": resp.get("cronExpression"),
        "next_run": resp.get("nextRun"),
        "last_run": resp.get("lastRun"),
        "timezone": resp.get("timezone"),
        "enabled": resp.get("enabled", True),
    }


def check_agent_resources(agent_name: str = "") -> dict:
    if agent_name:
        return get_ae_client().get(f"/api/v1/agents/{agent_name}/resources")
    return get_ae_client().get("/api/v1/agents/resources")


# ── Register dependency tools ──

tool_registry.register(
    ToolDefinition(
        name="get_workflow_dependencies",
        description=(
            "Get upstream and downstream dependencies for a workflow. "
            "Essential for tracing cascade failures to the root cause."
        ),
        category="dependency",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow to analyze",
            },
        },
        required_params=["workflow_name"],
    ),
    get_workflow_dependencies,
)

tool_registry.register(
    ToolDefinition(
        name="get_workflow_config",
        description=(
            "Get configuration details for a workflow: schedule, "
            "input/output paths, timeout, assigned agents."
        ),
        category="config",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow name",
            },
        },
        required_params=["workflow_name"],
    ),
    get_workflow_config,
)

tool_registry.register(
    ToolDefinition(
        name="get_schedule_info",
        description=(
            "Get schedule details for a workflow: cron expression, "
            "next/last run, timezone."
        ),
        category="config",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow name",
            },
        },
        required_params=["workflow_name"],
    ),
    get_schedule_info,
)

tool_registry.register(
    ToolDefinition(
        name="check_agent_resources",
        description=(
            "Check CPU and memory usage of AE agents. "
            "Helps identify resource-related failures."
        ),
        category="status",
        tier="read_only",
        parameters={
            "agent_name": {
                "type": "string",
                "description": "Agent name (empty for all)",
            },
        },
        required_params=[],
    ),
    check_agent_resources,
)
