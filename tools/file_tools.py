"""
File validation tools — check input/output file presence and format.
"""

import logging

from tools.base import ToolDefinition
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.files")


_NOT_SUPPORTED = (
    "Reading and writing workflow input/output files is not supported by this agent. "
    "Please check the file directly on the agent machine or the shared network path "
    "configured for this workflow."
)


def check_input_file(workflow_name: str, expected_date: str = "") -> dict:
    return {
        "supported": False,
        "workflow_name": workflow_name,
        "message": _NOT_SUPPORTED,
    }


def check_output_file(workflow_name: str, execution_id: str = "") -> dict:
    return {
        "supported": False,
        "workflow_name": workflow_name,
        "message": _NOT_SUPPORTED,
    }



# ── Register file tools ──

tool_registry.register(
    ToolDefinition(
        name="check_input_file",
        description=(
            "Check if the expected input file exists for a workflow, "
            "validate its format, and report file size and row count."
        ),
        category="file",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow that needs the input file",
            },
            "expected_date": {
                "type": "string",
                "description": "Expected date in YYYY-MM-DD format (optional)",
            },
        },
        required_params=["workflow_name"],
    ),
    check_input_file,
)

tool_registry.register(
    ToolDefinition(
        name="check_output_file",
        description=(
            "Check if the output file was produced by a workflow execution."
        ),
        category="file",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow name",
            },
            "execution_id": {
                "type": "string",
                "description": "Request ID (optional)",
            },
        },
        required_params=["workflow_name"],
    ),
    check_output_file,
)
