"""
Log & execution history tools.
"""

import logging

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.logs")


def get_execution_logs(execution_id: str, tail: int = 100) -> dict:
    resp = get_ae_client().get_execution_logs(execution_id=execution_id, tail=tail)
    
    # Handle ZIP content for T4 fallback
    if resp.get("is_zip") and resp.get("log_zip_content"):
        import io
        import zipfile
        import gzip
        try:
            with zipfile.ZipFile(io.BytesIO(resp["log_zip_content"])) as z:
                all_logs = []
                for name in z.namelist():
                    if name.lower().endswith(".gz"):
                        with z.open(name) as fz:
                            with gzip.GzipFile(fileobj=fz) as f:
                                content = f.read().decode("utf-8", errors="ignore")
                                all_logs.extend(content.splitlines()[-tail:])
                    elif name.lower().endswith(".log"):
                        with z.open(name) as f:
                            content = f.read().decode("utf-8", errors="ignore")
                            all_logs.extend(content.splitlines()[-tail:])
                
                return {
                    "execution_id": execution_id,
                    "workflow_name": resp.get("workflow_name", ""),
                    "logs": all_logs,
                    "log_count": len(all_logs),
                    "note": f"Extracted from T4 debug logs ({len(z.namelist())} files)"
                }
        except Exception as exc:
            logger.error("Failed to extract ZIP logs: %s", exc)
            return {"execution_id": execution_id, "error": f"Log extraction failed: {exc}", "logs": []}

    logs = resp.get("logs", [])
    return {
        "execution_id": execution_id,
        "workflow_name": resp.get("workflow_name", ""),
        "logs": logs,
        "log_count": len(logs),
    }


def get_execution_history(workflow_name: str, limit: int = 10) -> dict:
    execs = get_ae_client().get_workflow_instances(workflow_name=workflow_name, limit=limit)
    return {
        "workflow_name": workflow_name,
        "executions": execs,
        "total_count": len(execs),
    }


# ── Register log tools ──

tool_registry.register(
    ToolDefinition(
        name="get_execution_logs",
        description=(
            "Retrieve detailed logs for a specific workflow execution. "
            "Shows step-by-step execution trace, errors, and timestamps."
        ),
        category="logs",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The execution ID to get logs for",
            },
            "tail": {
                "type": "integer",
                "description": "Number of recent log lines (default 100)",
            },
        },
        required_params=["execution_id"],
        always_available=True,
    ),
    get_execution_logs,
)

tool_registry.register(
    ToolDefinition(
        name="get_execution_history",
        description=(
            "List the last N executions for a workflow, including status, "
            "duration, and timestamps. Useful for identifying patterns."
        ),
        category="logs",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Workflow name",
            },
            "limit": {
                "type": "integer",
                "description": "Max executions to return (default 10)",
            },
        },
        required_params=["workflow_name"],
    ),
    get_execution_history,
)
