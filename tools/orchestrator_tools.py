"""
Studio ↔ Orchestrator integration tools.

Registers two tools into the Studio tool registry:

  list_workflows   — discover saved DAGs by name and description
  run_workflow     — execute a DAG and return its output context
"""
from __future__ import annotations

import json
import logging
from typing import Any

from tools.base import ToolDefinition
from tools.registry import tool_registry
from tools.orchestrator_client import get_orchestrator_client

logger = logging.getLogger("ops_agent.orchestrator_tools")

_MAX_OUTPUT_CHARS = 4_000  # cap large context_json before returning to the LLM


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _list_workflows(**_: Any) -> str:
    client = get_orchestrator_client()
    try:
        workflows = client.list_workflows()
    except Exception as exc:
        logger.warning("list_workflows failed: %s", exc)
        return f"Could not reach the orchestrator: {exc}"

    if not workflows:
        return "No workflows found in the orchestrator."

    items = [
        {
            "id": str(wf["id"]),
            "name": wf["name"],
            "description": wf.get("description") or "",
        }
        for wf in workflows
    ]
    return json.dumps(items, indent=2)


def _run_workflow(
    workflow_id: str,
    trigger_payload: str = "{}",
    timeout_seconds: int = 120,
    **_: Any,
) -> str:
    try:
        payload: dict[str, Any] = json.loads(trigger_payload) if trigger_payload else {}
    except json.JSONDecodeError as exc:
        return f"Invalid trigger_payload — must be valid JSON: {exc}"

    client = get_orchestrator_client()
    try:
        ctx = client.run_and_wait(workflow_id, payload, timeout=timeout_seconds)
    except (RuntimeError, TimeoutError) as exc:
        return f"Workflow execution error: {exc}"
    except Exception as exc:
        logger.exception("Unexpected error running workflow %s", workflow_id)
        return f"Unexpected error: {exc}"

    output = json.dumps(ctx.get("context_json", {}), default=str)
    if len(output) > _MAX_OUTPUT_CHARS:
        output = output[:_MAX_OUTPUT_CHARS] + "... [truncated]"
    return f"Workflow completed.\n{output}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

tool_registry.register(
    ToolDefinition(
        name="list_workflows",
        description=(
            "List all saved workflows available in the visual orchestrator. "
            "Returns each workflow's UUID, name, and description. "
            "Use this to discover workflow IDs before calling run_workflow."
        ),
        category="orchestrator",
        tier="read_only",
        parameters={},
        required_params=[],
        always_available=True,
        use_when=(
            "The user asks what automated workflows exist, or you need to find "
            "a workflow ID before executing one."
        ),
        avoid_when="You already have the workflow ID.",
        input_examples=[{}],
    ),
    _list_workflows,
)

tool_registry.register(
    ToolDefinition(
        name="run_workflow",
        description=(
            "Execute a saved workflow in the visual orchestrator and wait for "
            "its result. Pass the workflow UUID and an optional JSON payload as "
            "trigger input. Returns the final output context when the workflow "
            "completes. Use list_workflows first to discover available IDs."
        ),
        category="orchestrator",
        tier="medium_risk",
        parameters={
            "workflow_id": {
                "type": "string",
                "description": "UUID of the workflow to execute (from list_workflows).",
            },
            "trigger_payload": {
                "type": "string",
                "description": (
                    "Input data for the workflow as a JSON string, e.g. "
                    "'{\"ticket_id\": \"INC-123\", \"severity\": \"high\"}'. "
                    "Pass '{}' if the workflow needs no input."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Maximum seconds to wait for completion. Default: 120.",
            },
        },
        required_params=["workflow_id"],
        always_available=False,
        use_when=(
            "The user wants to run a specific automated workflow, or you need to "
            "delegate a multi-step automation task to a pre-built DAG."
        ),
        avoid_when=(
            "A simpler typed tool already handles the task, or you don't have "
            "the workflow ID yet (call list_workflows first)."
        ),
        input_examples=[
            {
                "workflow_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "trigger_payload": '{"incident_id": "INC-456"}',
                "timeout_seconds": 90,
            }
        ],
    ),
    _run_workflow,
)
