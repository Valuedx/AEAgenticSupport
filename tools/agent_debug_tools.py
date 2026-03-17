"""
Sync tools for agent debug log extraction and analysis.
Used by the Orchestrator for conversational log diagnostics.
"""

import logging
import io
import zipfile
import time
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from config.llm_client import llm_client
from tools.base import get_ae_client
from tools.registry import tool_registry, ToolDefinition

logger = logging.getLogger("ops_agent.tools.agent_debug")

# Helper function for JSON serialization, assuming it's available elsewhere or needs to be defined.
# For this context, we'll provide a basic implementation.
import json
def _safe_json(data: Any) -> str:
    """Safely converts data to a JSON string."""
    try:
        return json.dumps(data)
    except TypeError:
        # Fallback for non-serializable objects, e.g., just return a string representation
        return str(data)

def analyze_agent_logs(
    agent_id: str,
    from_date: str | int = "",
    to_date: str | int = "",
    tail_lines: int = 100,
    **kwargs: Any
) -> dict:
    """
    Extract and analyze agent logs for a specific period.
    Handles multiple date-indexed log files within the ZIP and provides solution summaries.
    """
    import io
    import zipfile
    import time
    import re
    from datetime import datetime, timedelta

    client = get_ae_client()
    
    # 0. Resolve Agent
    agents = client.list_agents()
    match = next(
        (a for a in agents 
         if str(a.get("agentId") or a.get("id")) == agent_id 
         or str(a.get("agentName") or a.get("name")).lower() == agent_id.lower()), 
        None
    )

    # 1. Check Status
    if not match:
        return {"error": f"Agent '{agent_id}' not found"}
    
    agent_uuid = match.get("uuid")
    state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
    
    if state not in ("RUNNING", "CONNECTED", "ACTIVE"):
        agent_name = match.get("agentName") or match.get("name") or agent_id
        return {
            "error": f"Please restart your agent {agent_name} first",
            "agent_state": state
        }

    # 2. Parse Dates
    now_ms = int(time.time() * 1000)
    retention_days = 14 # Use 14 instead of 15 to stay safely within server limits
    min_date = datetime.now() - timedelta(days=retention_days)
    warning_retention = False

    try:
        if from_date:
            f_dt = datetime.fromisoformat(from_date) if isinstance(from_date, str) else datetime.fromtimestamp(from_date/1000)
            if f_dt < min_date:
                f_dt = min_date
                warning_retention = True
        else:
            f_dt = datetime.now() - timedelta(hours=24)
        
        if to_date:
            t_dt = datetime.fromisoformat(to_date) if isinstance(to_date, str) else datetime.fromtimestamp(to_date/1000)
            if t_dt > datetime.now():
                t_dt = datetime.now()
        else:
            t_dt = datetime.now()
            
        f_ms = int(f_dt.timestamp() * 1000)
        t_ms = int(t_dt.timestamp() * 1000)

        # Store original dates for ZIP filtering (internal to this tool)
        orig_f_dt, orig_t_dt = f_dt, t_dt

        # 2.5 Ensure span does not exceed 5 days (AE-1602)
        warning_span = False
        max_span = timedelta(days=5)
        if (t_dt - f_dt) > max_span:
            f_dt = t_dt - max_span
            f_ms = int(f_dt.timestamp() * 1000)
            warning_span = True
    except Exception as e:
        return {"success": False, "error": f"Invalid date format: {e}"}

    # 3. Request Logs
    logger.info("Requesting logs for agent %s (%s) from %s to %s", agent_id, agent_uuid, f_ms, t_ms)
    try:
        req_resp = client.request_agent_debug_logs(agent_uuid, f_ms, t_ms)
        req_id = req_resp.get("id")
    except Exception as e:
        return {"success": False, "error": f"Failed to initiate log extraction: {e}"}
        
    if not req_id:
        return {"success": False, "error": "Failed to initiate log extraction", "raw": req_resp}

    # 4. Polling
    logger.info("Polling for log request %s completion...", req_id)
    max_polls = 10
    log_file_link = None
    zip_bytes_direct = None

    for i in range(max_polls):
        time.sleep(10)
        try:
            status_resp = client.get_agent_debug_logs(str(req_id))
            if isinstance(status_resp, dict) and status_resp.get("is_zip"):
                zip_bytes_direct = status_resp.get("log_zip_content")
                break
            if isinstance(status_resp, dict) and status_resp.get("status") == "COMPLETE":
                log_file_link = status_resp.get("logFileLink")
                break
            if isinstance(status_resp, dict) and status_resp.get("status") in ("FAILED", "ERROR"):
                return {"success": False, "error": "Log extraction failed on server", "details": status_resp}
        except Exception as e:
            logger.warning("Poll error: %s", e)
    
    if not log_file_link and not zip_bytes_direct:
        return {"success": False, "error": "Timed out waiting for logs to be ready after 100s", "request_id": req_id}

        # 5. Download and Extract
    try:
        import gzip
        if zip_bytes_direct:
            zip_bytes = zip_bytes_direct
        else:
            zip_data = client._authorized_request("GET", log_file_link, use_rest_prefix=False)
            if isinstance(zip_data, dict) and zip_data.get("is_zip"):
                 zip_bytes = zip_data.get("log_zip_content")
            else:
                 zip_bytes = zip_data
             
        if not isinstance(zip_bytes, (bytes, bytearray)):
            return {"success": False, "error": "Failed to download log ZIP (received unexpected format)"}

        log_summary = []
        error_found = False
        all_errors = []
        
        # Date regex for filenames like agent.log.2026-03-16 or agent.log.20260316
        date_pattern = re.compile(r"(\d{4}-?\d{2}-?\d{2})")

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            all_files_in_zip = z.namelist()
            logger.info("Extracting %d files from log ZIP for agent %s", len(all_files_in_zip), agent_id)
            
            # Filter all relevant log-like files
            eligible_files = [n for n in all_files_in_zip if ".log" in n or "stdout" in n or "stderr" in n]
            
            for name in eligible_files:
                # Filter by date if filename contains one
                match_dt = date_pattern.search(name)
                if match_dt:
                    try:
                        raw_date = match_dt.group(1).replace("-", "")
                        file_dt = datetime.strptime(raw_date, "%Y%m%d")
                        # Allow files within range +/- 1 day for boundary overlaps
                        if file_dt.date() < (orig_f_dt.date() - timedelta(days=1)) or file_dt.date() > (orig_t_dt.date() + timedelta(days=1)):
                            continue
                    except ValueError:
                        pass

                try:
                    with z.open(name) as f:
                        if name.lower().endswith(".gz"):
                            with gzip.GzipFile(fileobj=f) as gz:
                                text = gz.read().decode("utf-8", errors="ignore")
                        else:
                            text = f.read().decode("utf-8", errors="ignore")
                            
                        lines = text.splitlines()
                        tail = lines[-tail_lines:]
                        content = "\n".join(tail)
                        
                        errors = [l for l in tail if any(x in l.upper() for x in ("ERROR", "FATAL", "EXCEPTION"))]
                        if errors:
                            error_found = True
                            all_errors.extend(errors)
                        
                        log_summary.append({
                            "filename": name,
                            "entries_analyzed": len(tail),
                            "errors_found": len(errors),
                            "content": content,
                            "full_lines": tail # Store full lines for context extraction
                        })
                except Exception as ef:
                    logger.warning("Could not read file %s in ZIP: %s", name, ef)

        # Generate report
        report_lines = [f"### Log Analysis for Agent: {agent_id}"]
        report_lines.append(f"**Period:** {f_dt.strftime('%Y-%m-%d %H:%M:%S')} to {t_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if warning_retention or warning_span:
            report_lines.append("\n> [!IMPORTANT]")
            if warning_retention:
                report_lines.append(f"> Requested start date was adjusted to {f_dt.strftime('%Y-%m-%d %H:%M:%S')} due to AutomationEdge's 14-day log retention policy.")
            if warning_span:
                report_lines.append(f"> The requested period was capped to 5 days ({f_dt.strftime('%Y-%m-%d %H:%M:%S')} to {t_dt.strftime('%Y-%m-%d %H:%M:%S')}) due to AutomationEdge platform limits.")
        
        report_lines.append(f"**Files Analyzed:** {len(log_summary)}")
        
        # Group by date and collect unique error snippets
        by_date = {}
        for entry in log_summary:
            if not entry["errors_found"]: continue
            
            # Extract date for grouping
            dt_str = "Unknown Date"
            match_dt = date_pattern.search(entry["filename"])
            if match_dt:
                dt_str = match_dt.group(1)
            
            if dt_str not in by_date: by_date[dt_str] = []
            
            # Extract unique snippets (first 3 unique) with 2 lines of context
            unique_snippets = []
            seen_snippets = set()
            raw_lines = entry.get("full_lines", [])
            for idx, line in enumerate(raw_lines):
                if any(x in line.upper() for x in ("ERROR", "FATAL", "EXCEPTION")):
                    # Capture ERROR + next 2 lines for context
                    snippet = "\n".join(raw_lines[idx : idx + 3])
                    
                    # Basic normalization for deduplication
                    norm = re.sub(r"\d", "X", line[:100])
                    if norm not in seen_snippets:
                        unique_snippets.append(snippet.strip())
                        seen_snippets.add(norm)
                    if len(unique_snippets) >= 3: break
            
            by_date[dt_str].append({
                "file": entry["filename"],
                "count": entry["errors_found"],
                "snippets": unique_snippets
            })

        # Generate AI Diagnostic Summary
        ai_diagnostic = ""
        if error_found:
            all_snippets_for_ai = []
            for dt_group in by_date.values():
                for f_info in dt_group:
                    all_snippets_for_ai.extend(f_info["snippets"])
            
            if all_snippets_for_ai:
                try:
                    logger.info("Generating AI diagnostic for %d snippets...", len(all_snippets_for_ai))
                    snippets_text = "\n---\n".join(all_snippets_for_ai[:5])
                    prompt = (
                        "Analyze these AutomationEdge agent log snippets. "
                        "Provide a 1-2 sentence plain-English summary of the issue "
                        "and a direct recommendation.\n\n"
                        f"Snippets:\n{snippets_text}"
                    )
                    ai_diagnostic = llm_client.chat(prompt, system="You are an expert technical support engineer. Be extremely concise.", max_tokens=200)
                    logger.info("AI Diagnostic generated: %s", ai_diagnostic[:50])
                except Exception as llm_err:
                    logger.error("Failed to generate AI diagnostic: %s", llm_err, exc_info=True)

        if not log_summary:
            report_lines.append("\n> [!WARNING]")
            report_lines.append("> No log files matching the date criteria were found in the retrieved ZIP.")
            status_msg = f"No relevant logs found for period {f_dt.date()} to {t_dt.date()}."
        elif not error_found:
            report_lines.append("\n### ✅ Clean Trace")
            report_lines.append("No critical errors (ERROR, FATAL, EXCEPTION) detected in the last 100 lines of the analyzed log files.")
            status_msg = "No critical issues detected in the recent logs."
        else:
            if ai_diagnostic:
                report_lines.append("\n### 🤖 AI Diagnostic Summary")
                report_lines.append(f"{ai_diagnostic}")

            report_lines.append("\n### ❌ Issues Detected (By Date)")
            # ... rest of the logic ...
            for dt_str in sorted(by_date.keys(), reverse=True):
                report_lines.append(f"\n#### 📅 {dt_str}")
                for item in by_date[dt_str]:
                    report_lines.append(f"- **{item['file']}** ({item['count']} errors):")
                    for snip in item["snippets"]:
                        # Use code block for snippets - NO leading spaces for the backticks!
                        report_lines.append(f"```log\n{snip}\n```")
            
            status_msg = f"Found {len(all_errors)} errors across {len([l for l in log_summary if l['errors_found'] > 0])} files."

        # Generate solutions
        suggested_solutions = []
        if error_found:
            error_text = "\n".join(all_errors).upper()
            if "CONNECTION" in error_text or "UNREACHABLE" in error_text:
                suggested_solutions.append("Network check: Ensure the agent machine can reach the AutomationEdge server (check proxy/firewall).")
            if "TIMEOUT" in error_text:
                suggested_solutions.append("Resources check: The agent might be under heavy load or target application is slow. Increase timeout settings.")
            if "AUTHENTICATION" in error_text or "401" in error_text:
                suggested_solutions.append("Credential check: Update the agent credentials or refresh the session in AE console.")
            if "DISK FULL" in error_text or "SPACE" in error_text:
                suggested_solutions.append("Disk space: Clear temporary files on the agent host machine.")
            if "MEMORY" in error_text or "HEAP" in error_text:
                suggested_solutions.append("Memory: Increase the Java Heap Size (-Xmx) in AEAgent.bat / AEAgent.conf.")
            
            if suggested_solutions:
                report_lines.append("\n### 💡 Suggested Solutions")
                for sol in suggested_solutions:
                    report_lines.append(f"- {sol}")

        full_report = "\n".join(report_lines)

        return {
            "success": True,
            "agent_id": agent_id,
            "period": f"{f_dt.date()} to {t_dt.date()}",
            "error_found": error_found,
            "logs": log_summary,
            "report": full_report,
            "summary": f"Analyzed {len(log_summary)} files. {'Issues detected.' if error_found else 'No issues found.'}",
            "suggested_solutions": suggested_solutions,
            "message": status_msg
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to process log ZIP: {e}"}

# Register tool
tool_registry.register(
    ToolDefinition(
        name="analyze_agent_logs",
        description=(
            "Extract and analyze AutomationEdge agent logs for a specific period. "
            "IMPORTANT: This tool requires a valid 'agent_id'. "
            "If the agent_id is unknown, you MUST FIRST call 'ae.agent.list_running' to show the user available options. "
            "Once an ID is provided, parse the dates into ISO format (YYYY-MM-DDTHH:MM:SS) using the 'current local time'. "
            "This tool is GUARDED: calling it will prompt the user to APPROVE the log extraction."
        ),
        category="diagnostics",
        tier="medium_risk",
        parameters={
            "agent_id": {
                "type": "string",
                "description": "REQUIRED: Agent name or ID. Call 'ae.agent.list_running' first if you don't have this.",
            },
            "from_date": {
                "type": "string",
                "description": "Start date and time (ISO format: YYYY-MM-DDTHH:MM:SS). Defaults to last 24h. Note: Server retains only 15 days of logs.",
            },
            "to_date": {
                "type": "string",
                "description": "End date and time (ISO format: YYYY-MM-DDTHH:MM:SS). Defaults to now.",
            },
            "tail_lines": {
                "type": "integer",
                "description": "Number of lines to read from the end (default 100).",
            },
        },
        required_params=[],
    ),
    analyze_agent_logs,
)
