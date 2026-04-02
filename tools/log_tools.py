"""
Log and execution history tools.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.logs")


ERROR_KEYWORDS = re.compile(
    r"\b(ERROR|FATAL|FAILURE|FAILED|EXCEPTION|PSQLException|"
    r"NullPointerException|StackOverflowError|OutOfMemoryError|"
    r"does not exist|relation .* does not exist|"
    r"An error occurred|UnknownHostException|"
    r"Workflow detected one or more steps with errors|"
    r"Workflow is killing)\b",
    re.IGNORECASE,
)

TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+\-]\d{2}:\d{2})"
)

LEVEL_RE = re.compile(r"\b(ERROR|FATAL|FAILURE|FAILED|WARN|INFO|DEBUG|TRACE)\b", re.IGNORECASE)
COMPONENT_RE = re.compile(r"\]\[([^\]]+)\]\s+(?:ERROR|FATAL|FAILURE|FAILED|WARN)", re.IGNORECASE)


def _normalize_log_line(entry: Any) -> str:
    if isinstance(entry, str):
        return entry

    if isinstance(entry, dict):
        timestamp = (
            entry.get("timestamp")
            or entry.get("time")
            or entry.get("createdDate")
            or entry.get("created_at")
            or ""
        )
        level = entry.get("level") or entry.get("severity") or entry.get("status") or ""
        component = (
            entry.get("component")
            or entry.get("logger")
            or entry.get("thread")
            or entry.get("step")
            or ""
        )
        message = (
            entry.get("message")
            or entry.get("log")
            or entry.get("text")
            or entry.get("details")
            or entry.get("stackTrace")
            or ""
        )

        parts = []
        if timestamp:
            parts.append(str(timestamp).strip())
        if component:
            parts.append(f"[{str(component).strip()}]")
        if level:
            parts.append(str(level).strip().upper())
        if message:
            parts.append(str(message).strip())
        if parts:
            return " ".join(part for part in parts if part)

    return str(entry)


def _normalize_log_lines(log_lines: list[Any]) -> list[str]:
    return [_normalize_log_line(entry) for entry in (log_lines or [])]


def _is_trigger_line(line: str) -> bool:
    if not line or not ERROR_KEYWORDS.search(line):
        return False

    # Prefer true AE log lines with timestamps to avoid stack-trace false positives.
    if TIMESTAMP_RE.match(line):
        return True

    # Fallback for direct API payloads that may not include timestamps but do include level.
    return bool(LEVEL_RE.search(line) and re.search(r"\b(ERROR|FATAL|FAILURE|FAILED)\b", line, re.IGNORECASE))


def _extract_error_message(line: str) -> str:
    msg_match = re.search(r"\s-\s(.+)$", line)
    if msg_match:
        return msg_match.group(1).strip()

    level_match = re.search(r"\b(?:ERROR|FATAL|FAILURE|FAILED)\b[:\s-]*(.+)$", line, re.IGNORECASE)
    if level_match:
        return level_match.group(1).strip()

    return line.strip()


def _normalize_error_signature(message: str) -> str:
    sig = str(message or "").strip().lower()
    sig = re.sub(r"https?://\S+", "<url>", sig)
    sig = re.sub(r"\b[a-f0-9]{8,}(?:-[a-f0-9]{4,})*\b", "<id>", sig, flags=re.IGNORECASE)
    sig = re.sub(r"\d+", "N", sig)
    sig = re.sub(r"\s+", " ", sig)
    return sig[:180]


def extract_error_blocks(log_lines: list[Any], context_lines: int = 50) -> list[dict]:
    """
    Scan the full log from the first line and return every detected error block.

    Rules:
    - A trigger line must be an actual AE log line, not a stack-trace continuation.
    - Every trigger gets its own block with up to `context_lines` following lines.
    - Overlapping windows are allowed; later triggers still become their own blocks.
    - Blocks are returned chronologically with stable error labels.
    """
    normalized_lines = _normalize_log_lines(log_lines)
    total_lines = len(normalized_lines)
    blocks: list[dict] = []

    for index, line in enumerate(normalized_lines):
        if not _is_trigger_line(line):
            continue

        end = min(index + context_lines + 1, total_lines)
        chunk = normalized_lines[index:end]

        timestamp_match = TIMESTAMP_RE.match(line)
        timestamp = timestamp_match.group(1) if timestamp_match else ""

        component_match = COMPONENT_RE.search(line)
        component = component_match.group(1).strip() if component_match else ""

        error_message = _extract_error_message(line)
        signature = _normalize_error_signature(error_message)
        secondary_errors = [item for item in chunk[1:] if _is_trigger_line(item)]

        blocks.append(
            {
                "timestamp": timestamp,
                "component": component,
                "error_message": error_message,
                "error_signature": signature,
                "trigger_line": line,
                "secondary_error_count": len(secondary_errors),
                "line_number": index + 1,
                "line_range": f"{index + 1}-{end}",
                "lines": chunk,
            }
        )

    blocks.sort(key=lambda block: (block["timestamp"] or "", block["line_number"]))

    total = len(blocks)
    for idx, block in enumerate(blocks, start=1):
        block["error_index"] = idx
        block["error_label"] = f"Error {idx} of {total}"

    return blocks


def _group_error_blocks(error_blocks: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}

    for block in error_blocks:
        signature = block.get("error_signature") or _normalize_error_signature(block.get("error_message", ""))
        if signature not in groups:
            groups[signature] = {
                "signature": signature,
                "error_message": block.get("error_message", ""),
                "occurrence_count": 0,
                "first_seen": block.get("timestamp", ""),
                "last_seen": block.get("timestamp", ""),
                "components": set(),
                "representative": block,
            }

        group = groups[signature]
        group["occurrence_count"] += 1

        timestamp = block.get("timestamp", "")
        if timestamp and (not group["first_seen"] or timestamp < group["first_seen"]):
            group["first_seen"] = timestamp
        if timestamp and (not group["last_seen"] or timestamp > group["last_seen"]):
            group["last_seen"] = timestamp

        component = block.get("component", "")
        if component:
            group["components"].add(component)

        if len(block.get("lines", [])) > len(group["representative"].get("lines", [])):
            group["representative"] = block

    grouped = []
    for group in groups.values():
        grouped.append(
            {
                "signature": group["signature"],
                "error_message": group["error_message"],
                "occurrence_count": group["occurrence_count"],
                "first_seen": group["first_seen"],
                "last_seen": group["last_seen"],
                "components": sorted(group["components"]),
                "representative": group["representative"],
            }
        )

    grouped.sort(key=lambda item: (item["first_seen"] or "", item["error_message"]))
    return grouped


def _build_upstream_hint(error_blocks: list[dict]) -> str:
    if len(error_blocks) < 2:
        return ""

    earliest = error_blocks[0].get("error_message", "")
    later_messages = " ".join(block.get("error_message", "") for block in error_blocks[1:])
    if (
        re.search(r"nullpointerexception|abort", later_messages, re.IGNORECASE)
        and not re.search(r"nullpointerexception|abort", earliest, re.IGNORECASE)
    ):
        return (
            "The earliest detected error appears before the later abort/null-pointer failures, "
            "so it may be the upstream cause while the later exception is secondary."
        )

    return ""


def _build_execution_log_report(
    *,
    execution_id: str,
    workflow_name: str,
    note: str,
    total_lines_scanned: int,
    error_blocks: list[dict],
    error_groups: list[dict],
) -> str:
    lines = ["### Execution Log Analysis"]
    lines.append(f"Execution ID: `{execution_id}`")
    if workflow_name:
        lines.append(f"Workflow: `{workflow_name}`")
    if note:
        lines.append(f"Source: {note}")
    lines.append(f"Lines scanned from start: {total_lines_scanned}")
    lines.append(f"Detected {len(error_blocks)} error block(s) across {len(error_groups)} distinct error pattern(s).")

    upstream_hint = _build_upstream_hint(error_blocks)
    if upstream_hint:
        lines.append("")
        lines.append(f"Upstream hint: {upstream_hint}")

    if error_groups:
        lines.append("")
        lines.append("**Distinct Error Patterns**")
        for idx, group in enumerate(error_groups, start=1):
            components = ", ".join(group["components"]) if group["components"] else "unknown component"
            seen_range = group["first_seen"] or "timestamp unavailable"
            if group["last_seen"] and group["last_seen"] != group["first_seen"]:
                seen_range = f"{seen_range} to {group['last_seen']}"
            lines.append(
                f"- Pattern {idx}: `{group['error_message']}` | occurrences: {group['occurrence_count']} | component: {components} | seen: {seen_range}"
            )

    if error_blocks:
        lines.append("")
        lines.append("**Chronological Error Blocks**")
        for block in error_blocks:
            timestamp = block.get("timestamp") or "timestamp unavailable"
            component = block.get("component") or "unknown component"
            lines.append(
                f"- {block['error_label']} | line {block['line_number']} | {timestamp} | {component}"
            )
            lines.append(f"  Trigger: `{block['error_message']}`")

            extracted_lines = block.get("lines", [])
            if extracted_lines:
                lines.append("  Extracted log lines (trigger + up to 50 following lines):")
                for item in extracted_lines:
                    lines.append(f"  {item}")

    return "\n".join(lines)


def get_execution_logs(execution_id: str, tail: int = 0, user_id: str = "", org_code: str = "") -> dict:
    # Logs intentionally use the admin/service-account side so investigations
    # can still fetch technical details even when read views are user-scoped.
    # Guard: check if the assigned agent is running before extracting logs
    try:
        from mcp_server.ae_client import get_ae_client as get_mcp_client

        client = get_mcp_client()
        try:
            request_data = client.get_request(execution_id)
        except Exception:
            request_data = {}

        agent_name = request_data.get("agentName") or request_data.get("agentId") or ""
        workflow_name = request_data.get("workflowName") or (request_data.get("workflowConfiguration") or {}).get("name") or ""

        if agent_name:
            try:
                agents = client.list_agents()
                for agent in agents:
                    agent_id = str(agent.get("agentId") or agent.get("id") or "")
                    resolved_name = str(agent.get("agentName") or agent.get("name") or agent_name)
                    if agent_id == str(agent_name) or resolved_name.lower() == str(agent_name).lower():
                        state = str(agent.get("agentState") or agent.get("state") or "UNKNOWN").upper()
                        if state not in ("CONNECTED", "RUNNING", "ACTIVE"):
                            logger.info(
                                "Agent guard blocked log access: agent=%s state=%s request=%s",
                                resolved_name,
                                state,
                                execution_id,
                            )
                            return {
                                "success": False,
                                "execution_id": execution_id,
                                "agent_offline": True,
                                "status": "blocked",
                                "error": (
                                    f"Cannot retrieve logs because the agent '{resolved_name}' is currently "
                                    f"{state}. Please restart the agent first and try again."
                                ),
                                "message": (
                                    f"The agent **{resolved_name}** is currently **{state}** "
                                    f"for workflow **{workflow_name or 'N/A'}**.\n\n"
                                    "Logs can only be extracted when the assigned agent is running. "
                                    "Please restart the agent and then request the logs again."
                                ),
                                "agent_name": resolved_name,
                                "agent_state": state,
                                "workflow_name": workflow_name,
                                "log_lines": [],
                                "error_blocks": [],
                            }
                        break
            except Exception:
                pass
    except Exception:
        pass

    resp = get_ae_client().get_execution_logs(execution_id=execution_id, tail=tail)

    if not resp:
        return {
            "success": False,
            "execution_id": execution_id,
            "status": "failure",
            "error": (
                "No logs found via primary or T4 debug channels. "
                "The execution might be too old or the server might have discarded the logs."
            ),
            "logs": [],
            "error_blocks": [],
        }

    if isinstance(resp, list):
        raw_logs = resp
        note = "Retrieved via direct API path"
        workflow_name = ""
    elif isinstance(resp, dict) and resp.get("is_zip") and resp.get("log_zip_content"):
        import gzip
        import io
        import zipfile

        try:
            raw_logs = []
            workflow_name = str(resp.get("workflow_name") or "")
            with zipfile.ZipFile(io.BytesIO(resp["log_zip_content"])) as archive:
                file_count = len(archive.namelist())
                for name in archive.namelist():
                    if name.lower().endswith(".gz"):
                        with archive.open(name) as compressed_file:
                            with gzip.GzipFile(fileobj=compressed_file) as log_file:
                                content = log_file.read().decode("utf-8", errors="ignore")
                    elif name.lower().endswith(".log"):
                        with archive.open(name) as log_file:
                            content = log_file.read().decode("utf-8", errors="ignore")
                    else:
                        continue

                    if not content:
                        continue

                    lines = content.splitlines()
                    raw_logs.extend(lines[-int(tail):] if tail and tail > 0 else lines)

            note = f"Extracted from T4 debug logs ({file_count} files). {resp.get('source_info', '')}".strip()
        except Exception as exc:
            logger.error("Failed to extract ZIP logs: %s", exc)
            return {
                "success": False,
                "execution_id": execution_id,
                "error": f"Log extraction failed: {exc}",
                "logs": [],
                "error_blocks": [],
            }
    else:
        raw_logs = resp.get("logs", []) if isinstance(resp, dict) else []
        workflow_name = str(resp.get("workflow_name") or "") if isinstance(resp, dict) else ""
        note = f"Source: {resp.get('source_info', 'AE direct API')}" if isinstance(resp, dict) else ""

        if not raw_logs:
            return {
                "success": False,
                "execution_id": execution_id,
                "status": "failure",
                "error": (
                    "Log retrieval returned success but the log list is empty. "
                    "This can happen if the execution failed before logs could be flushed. "
                    "Advice: trigger the workflow again or check agent service health."
                ),
                "logs": [],
                "error_blocks": [],
            }

    normalized_logs = _normalize_log_lines(raw_logs)
    error_blocks = extract_error_blocks(normalized_logs, context_lines=50)
    error_groups = _group_error_blocks(error_blocks)

    if error_blocks:
        report = _build_execution_log_report(
            execution_id=execution_id,
            workflow_name=workflow_name,
            note=note,
            total_lines_scanned=len(normalized_logs),
            error_blocks=error_blocks,
            error_groups=error_groups,
        )
        return {
            "success": True,
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "logs": normalized_logs,
            "log_count": len(normalized_logs),
            "note": note,
            "scan_mode": "full_log" if tail == 0 else "tail_scan",
            "lines_scanned": len(normalized_logs),
            "error_block_count": len(error_blocks),
            "distinct_error_count": len(error_groups),
            "error_groups": error_groups,
            "error_blocks": error_blocks,
            "primary_error": error_blocks[0],
            "latest_error": error_blocks[-1],
            "report": report,
        }

    tail_logs = normalized_logs[-100:] if len(normalized_logs) > 100 else normalized_logs
    return {
        "success": True,
        "execution_id": execution_id,
        "workflow_name": workflow_name,
        "logs": tail_logs,
        "log_count": len(tail_logs),
        "note": note,
        "scan_mode": "full_log" if tail == 0 else "tail_scan",
        "lines_scanned": len(normalized_logs),
        "error_block_count": 0,
        "distinct_error_count": 0,
        "error_blocks": [],
        "error_groups": [],
        "tail_note": (
            f"No errors detected. Showing last {len(tail_logs)} of {len(normalized_logs)} total log lines."
        ),
        "report": (
            "### Execution Log Analysis\n"
            f"Execution ID: `{execution_id}`\n"
            + (f"Workflow: `{workflow_name}`\n" if workflow_name else "")
            + f"Source: {note}\n"
            + f"Lines scanned from start: {len(normalized_logs)}\n"
            + "No error blocks were detected in the execution log."
        ),
    }


def get_execution_history(workflow_name: str, limit: int = 10) -> dict:
    executions = get_ae_client().get_workflow_instances(workflow_name=workflow_name, limit=limit)
    return {
        "success": True,
        "workflow_name": workflow_name,
        "executions": executions,
        "total_count": len(executions),
    }


tool_registry.register(
    ToolDefinition(
        name="get_execution_logs",
        description=(
            "Retrieve technical logs for a specific workflow execution/request ID. "
            "Use this only when you have a numeric Request/Execution ID. "
            "The tool scans the full execution log from the first line by default, "
            "extracts every distinct error block in chronological order, groups repeated failures, "
            "and returns a structured report with the full error chain."
        ),
        category="logs",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The numeric execution ID or request ID (for example 2501865).",
            },
            "tail": {
                "type": "integer",
                "description": "Optional line limit. Use 0 to scan the full execution log; this is the default.",
            },
        },
        required_params=["execution_id"],
        always_available=True,
        use_when=(
            "The user provides a specific Request ID or Execution ID and asks for "
            "the logs, errors, trace, or root cause for that single run."
        ),
        avoid_when=(
            "The user asks for agent logs, a date range, or logs for a workflow name without an execution ID. "
            "For those, use analyze_agent_logs or check_workflow_status first."
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
                "description": "Workflow name.",
            },
            "limit": {
                "type": "integer",
                "description": "Max executions to return (default 10).",
            },
        },
        required_params=["workflow_name"],
    ),
    get_execution_history,
)
