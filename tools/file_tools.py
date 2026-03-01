"""
File validation tools — check input/output file presence and format.
"""

import logging

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.files")


def check_input_file(workflow_name: str, expected_date: str = "") -> dict:
    params = {"date": expected_date} if expected_date else None
    resp = get_ae_client().get(
        f"/api/v1/workflows/{workflow_name}/input-file",
        params=params,
    )
    return {
        "workflow_name": workflow_name,
        "file_exists": resp.get("exists", False),
        "file_path": resp.get("filePath"),
        "file_size": resp.get("fileSize"),
        "last_modified": resp.get("lastModified"),
        "expected_date": expected_date,
        "format_valid": resp.get("formatValid"),
        "row_count": resp.get("rowCount"),
    }


def check_output_file(workflow_name: str, execution_id: str = "") -> dict:
    params = {}
    if execution_id:
        params["executionId"] = execution_id
    resp = get_ae_client().get(
        f"/api/v1/workflows/{workflow_name}/output-file",
        params=params,
    )
    return {
        "workflow_name": workflow_name,
        "file_exists": resp.get("exists", False),
        "file_path": resp.get("filePath"),
        "file_size": resp.get("fileSize"),
        "last_modified": resp.get("lastModified"),
        "row_count": resp.get("rowCount"),
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
                "description": "Execution ID (optional)",
            },
        },
        required_params=["workflow_name"],
    ),
    check_output_file,
)
