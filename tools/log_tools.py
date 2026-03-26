"""
Log & execution history tools.
"""

import logging
import re

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.logs")

# ── Keywords that signal an error block ──
ERROR_KEYWORDS = re.compile(
    r"\b(ERROR|FATAL|FAILURE|FAILED|EXCEPTION|PSQLException|"
    r"NullPointerException|StackOverflowError|OutOfMemoryError|"
    r"does not exist|relation .* does not exist|"
    r"An error occurred|Workflow detected one or more steps with errors|"
    r"Workflow is killing)\b",
    re.IGNORECASE,
)

TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[+\-]\d{2}:\d{2})"
)


def extract_error_blocks(log_lines: list[str], context_lines: int = 50) -> list[dict]:
    """
    Scan log_lines row by row.

    Each ERROR trigger line gets its OWN independent block of up to
    `context_lines` following lines (stack trace / context).

    Rules:
    - Every trigger is its own chunk — no merging even if windows overlap.
    - If a later trigger falls inside a previous block's window, it still
      gets its own fresh block starting from itself.
    - All blocks are returned sorted chronologically by their trigger timestamp.
    - Each block carries an `error_index` (1-based) so the caller knows
      "Error 1 of 4", "Error 2 of 4", etc.
    """
    n = len(log_lines)
    result = []

    for i, line in enumerate(log_lines):
        if not ERROR_KEYWORDS.search(line):
            continue

        # Capture this trigger + next `context_lines` lines as its own block
        end = min(i + context_lines + 1, n)
        chunk = log_lines[i:end]

        # Timestamp from the trigger line itself
        ts_match = TIMESTAMP_RE.match(line)
        timestamp = ts_match.group(1) if ts_match else ""

        # Pull step/component name from log line if present
        # Format: [exec_id][ComponentName] LEVEL ...
        component_match = re.search(r"\]\[([^\]]+)\]\s+(?:ERROR|FATAL)", line, re.IGNORECASE)
        component = component_match.group(1).strip() if component_match else ""

        # Count how many additional error lines exist within this chunk
        # (secondary errors inside the same stack trace window)
        secondary_errors = [
            l for l in chunk[1:] if ERROR_KEYWORDS.search(l)
        ]

        result.append({
            "timestamp": timestamp,
            "component": component,
            "trigger_line": line,
            "secondary_error_count": len(secondary_errors),
            "line_range": f"{i + 1}–{end}",
            "lines": chunk,
        })

    # Sort chronologically — ISO-8601 timestamps sort correctly as strings
    result.sort(key=lambda b: b["timestamp"])

    # Attach 1-based index AFTER sorting so order matches date sequence
    for idx, block in enumerate(result, start=1):
        block["error_index"] = idx
        block["error_label"] = f"Error {idx} of {len(result)}"

    return result


def get_execution_logs(execution_id: str, tail: int = 100) -> dict:
    # Guard: check if the assigned agent is running before extracting logs
    try:
        from mcp_server.ae_client import get_ae_client as get_mcp_client
        client = get_mcp_client()
        try:
            request_data = client.get_request(execution_id)
        except Exception:
            request_data = {}

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

        if agent_name:
            try:
                agents = client.list_agents()
                for a in agents:
                    aid = str(a.get("agentId") or a.get("id") or "")
                    aname = str(a.get("agentName") or a.get("name") or "")
                    if aid == agent_name or aname.lower() == agent_name.lower():
                        state = (a.get("agentState") or a.get("state") or "UNKNOWN").upper()
                        resolved_name = a.get("agentName") or a.get("name") or agent_name
                        if state not in ("CONNECTED", "RUNNING", "ACTIVE"):
                            logger.info(
                                "Agent guard blocked log access: agent=%s state=%s request=%s",
                                resolved_name, state, execution_id,
                            )
                            return {
                                "execution_id": execution_id,
                                "agent_offline": True,
                                "status": "blocked",
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
                                "log_lines": [],
                                "error_blocks": [],
                            }
                        break
            except Exception:
                pass  # Don't block log access if agent lookup fails
    except Exception:
        pass  # Guard is best-effort; never block on import/lookup failures

    resp = get_ae_client().get_execution_logs(execution_id=execution_id, tail=tail)

    if not resp:
        return {
            "execution_id": execution_id,
            "status": "failure",
            "error": (
                "No logs found via primary or T4 debug channels. "
                "The execution might be too old or the server might have discarded the logs."
            ),
            "log_lines": [],
            "error_blocks": [],
        }

    # ── Phase 1: List response (direct API) ──
    if isinstance(resp, list):
        raw_logs = resp
        note = "Retrieved via direct API path"
        workflow_name = ""

    # ── Phase 2: ZIP / T4 fallback ──
    elif isinstance(resp, dict) and resp.get("is_zip") and resp.get("log_zip_content"):
        import io
        import zipfile
        import gzip

        try:
            raw_logs = []
            workflow_name = resp.get("workflow_name", "")
            with zipfile.ZipFile(io.BytesIO(resp["log_zip_content"])) as z:
                file_count = len(z.namelist())
                for name in z.namelist():
                    if name.lower().endswith(".gz"):
                        with z.open(name) as fz:
                            with gzip.GzipFile(fileobj=fz) as f:
                                content = f.read().decode("utf-8", errors="ignore")
                                if content:
                                    lines = content.splitlines()
                                    raw_logs.extend(
                                        lines[-int(tail):] if tail else lines
                                    )
                    elif name.lower().endswith(".log"):
                        with z.open(name) as f:
                            content = f.read().decode("utf-8", errors="ignore")
                            if content:
                                lines = content.splitlines()
                                raw_logs.extend(
                                    lines[-int(tail):] if tail else lines
                                )
            note = (
                f"Extracted from T4 debug logs ({file_count} files). "
                f"{resp.get('source_info', '')}"
            )
        except Exception as exc:
            logger.error("Failed to extract ZIP logs: %s", exc)
            return {
                "execution_id": execution_id,
                "error": f"Log extraction failed: {exc}",
                "logs": [],
                "error_blocks": [],
            }

    # ── Phase 3: Standard dict response ──
    else:
        raw_logs = resp.get("logs", []) if isinstance(resp, dict) else []
        workflow_name = resp.get("workflow_name", "") if isinstance(resp, dict) else ""
        note = f"Source: {resp.get('source_info', 'AE direct API')}" if isinstance(resp, dict) else ""

        if not raw_logs:
            return {
                "execution_id": execution_id,
                "status": "failure",
                "error": (
                    "Log retrieval returned success but the log list is empty. "
                    "This can happen if the execution failed before logs could be flushed. "
                    "Advice: Trigger the bot again or check agent service health."
                ),
                "logs": [],
                "error_blocks": [],
            }

    # ── Error block extraction (shared for all paths) ──
    error_blocks = extract_error_blocks(raw_logs, context_lines=50)

    if error_blocks:
        # Errors found — return full logs + structured error blocks
        return {
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "logs": raw_logs,
            "log_count": len(raw_logs),
            "note": note,
            "error_block_count": len(error_blocks),
            "error_blocks": error_blocks,   # list of {timestamp, trigger_line, lines[]}
        }
    else:
        # No errors found — return only the tail 100 lines for a clean summary
        tail_logs = raw_logs[-100:] if len(raw_logs) > 100 else raw_logs
        return {
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "logs": tail_logs,
            "log_count": len(tail_logs),
            "note": note,
            "error_block_count": 0,
            "error_blocks": [],
            "tail_note": (
                f"No errors detected. Showing last {len(tail_logs)} of "
                f"{len(raw_logs)} total log lines."
            ),
        }


def get_execution_history(workflow_name: str, limit: int = 10) -> dict:
    execs = get_ae_client().get_workflow_instances(
        workflow_name=workflow_name, limit=limit
    )
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
            "Retrieve technical logs for a SPECIFIC workflow execution/request ID. "
            "Use this ONLY when you have a numeric Request/Execution ID (e.g., 2501865). "
            "This tool provides the step-by-step execution trace for that single run. "
            "IMPORTANT: It does NOT require a date/time range; it fetches logs for the "
            "entire life of that specific request ID. "
            "The response includes an 'error_blocks' field: each block contains the "
            "ERROR/FAILURE trigger line plus up to 50 following lines (stack trace), "
            "sorted chronologically."
        ),
        category="logs",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The numeric execution ID or request ID (e.g. 2501865) to get logs for.",
            },
            "tail": {
                "type": "integer",
                "description": "Number of recent log lines (default 100)",
            },
        },
        required_params=["execution_id"],
        always_available=True,
        use_when=(
            "The user provides a specific Request ID or Execution ID and asks for "
            "the logs, errors, or trace for that specific run."
        ),
        avoid_when=(
            "The user asks for 'agent logs' or 'logs for a date range' or "
            "'logs for a bot name' without an ID. For those, use "
            "'analyze_agent_logs' or 'check_workflow_status' first."
        ),
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