"""
P0 agent_read (9) + agent_mutate (2) tools — 11 total.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client
from config.llm_client import llm_client

logger = logging.getLogger("ae_mcp.tools.agent")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ═══════════════════════════════════════════════════════════════════════
#  agent_read
# ═══════════════════════════════════════════════════════════════════════

async def agent_list_stopped() -> Any:
    """List all agents in Stopped state."""
    agents = get_ae_client().list_agents()
    stopped = [
        a for a in agents
        if (a.get("agentState") or a.get("state") or "").upper() in ("STOPPED", "DISCONNECTED", "OFFLINE")
    ]
    items = []
    for a in stopped:
        items.append({
            "agent_id": a.get("agentId") or a.get("id"),
            "agent_name": a.get("agentName") or a.get("name"),
            "state": a.get("agentState") or a.get("state"),
            "last_seen": a.get("lastSeen") or a.get("lastHeartbeat"),
            "controller": a.get("controllerName") or a.get("controller"),
        })
    return {"stopped_agents": items, "count": len(items)}


async def agent_list_unknown() -> Any:
    """List agents in unknown or unrecognized state."""
    agents = get_ae_client().list_agents()
    known_states = {"CONNECTED", "RUNNING", "ACTIVE", "STOPPED", "DISCONNECTED", "OFFLINE", "IDLE"}
    unknown = [
        a for a in agents
        if (a.get("agentState") or a.get("state") or "UNKNOWN").upper() not in known_states
    ]
    items = []
    for a in unknown:
        items.append({
            "agent_id": a.get("agentId") or a.get("id"),
            "agent_name": a.get("agentName") or a.get("name"),
            "state": a.get("agentState") or a.get("state"),
            "last_seen": a.get("lastSeen") or a.get("lastHeartbeat"),
        })
    return {"unknown_agents": items, "count": len(items)}


async def agent_get_status(agent_id: str) -> str:
    """Get the current status of a specific agent."""
    agents = get_ae_client().list_agents()
    match = None
    for a in agents:
        aid = a.get("agentId") or a.get("id") or ""
        aname = a.get("agentName") or a.get("name") or ""
        if str(aid) == agent_id or aname == agent_id:
            logger.info("Matched agent %s (ID: %s) by name or ID", aname, aid)
            match = a
            break
    
    logger.info("Search loop finished. match=%s", match is not None)

    if not match:
        try:
            match = get_ae_client().get_agent(agent_id)
        except Exception:
            return {"error": f"Agent '{agent_id}' not found"}
    state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
    return {
        "agent_id": match.get("agentId") or match.get("id"),
        "agent_name": match.get("agentName") or match.get("name"),
        "state": state,
        "is_healthy": state in ("CONNECTED", "RUNNING", "ACTIVE"),
        "last_seen": match.get("lastSeen") or match.get("lastHeartbeat"),
        "version": match.get("version") or match.get("agentVersion"),
        "os": match.get("os") or match.get("operatingSystem"),
    }


async def agent_get_details(agent_id: str) -> str:
    """Get full details of an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            {"error": f"Agent '{agent_id}' not found"},
        )
    return _safe_json(data)


async def agent_get_current_load(agent_id: str) -> str:
    """Get the active workload summary for an agent."""
    agents = get_ae_client().list_agents()
    match = None
    for a in agents:
        if (a.get("agentId") or a.get("id") or "") == agent_id or \
           (a.get("agentName") or a.get("name") or "") == agent_id:
            match = a
            break

    running_count = 0
    try:
        running = get_ae_client().get_agent_requests(agent_id)
        running_count = len(running)
    except Exception:
        pass

    if match:
        return _safe_json({
            "agent_id": agent_id,
            "agent_name": match.get("agentName") or match.get("name"),
            "state": match.get("agentState") or match.get("state"),
            "running_requests": running_count,
            "max_concurrent": match.get("maxConcurrent") or match.get("concurrency"),
            "cpu_usage": match.get("cpuUsage"),
            "memory_usage": match.get("memoryUsage"),
        })
    return _safe_json({"agent_id": agent_id, "running_requests": running_count})


async def agent_get_running_requests(agent_id: str, limit: int = 50) -> str:
    """Get requests currently executing on an agent."""
    try:
        requests = get_ae_client().get_agent_requests(agent_id, limit=limit)
    except Exception:
        all_requests = get_ae_client().search_requests(
            filters={"agentName": agent_id, "status": "Running"}, limit=limit
        )
        requests = all_requests

    items = []
    for r in requests[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "status": r.get("status"),
            "created": r.get("createdDate"),
            "user": r.get("userId"),
        })
    return _safe_json({"agent_id": agent_id, "running_requests": items, "count": len(items)})


async def agent_get_assigned_workflows(agent_id: str) -> str:
    """Get workflows assigned to an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
        workflows = data.get("assignedWorkflows") or data.get("workflows") or []
    except Exception:
        workflows = []

    items = []
    for w in workflows:
        if isinstance(w, dict):
            items.append({
                "workflow_id": w.get("workflowId") or w.get("id"),
                "workflow_name": w.get("workflowName") or w.get("name"),
                "active": w.get("active"),
            })
        elif isinstance(w, str):
            items.append({"workflow_name": w})

    return _safe_json({"agent_id": agent_id, "assigned_workflows": items, "count": len(items)})


async def agent_get_connectivity_state(agent_id: str) -> str:
    """Check agent connectivity to controller and platform."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            {},
        )

    state = (data.get("agentState") or data.get("state") or "UNKNOWN").upper()
    return _safe_json({
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "state": state,
        "connected_to_controller": state in ("CONNECTED", "RUNNING", "ACTIVE"),
        "controller": data.get("controllerName") or data.get("controller"),
        "controller_state": data.get("controllerState"),
        "platform_reachable": data.get("platformReachable"),
        "last_heartbeat": data.get("lastSeen") or data.get("lastHeartbeat"),
    })


async def agent_get_rdp_session_state(agent_id: str) -> str:
    """Check RDP/desktop session state for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            {},
        )

    return _safe_json({
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "rdp_session_active": data.get("rdpSessionActive") or data.get("desktopSessionActive"),
        "rdp_user": data.get("rdpUser") or data.get("desktopUser"),
        "screen_resolution": data.get("screenResolution"),
        "locked": data.get("screenLocked") or data.get("isLocked"),
    })


# ═══════════════════════════════════════════════════════════════════════
#  agent_mutate
# ═══════════════════════════════════════════════════════════════════════

async def agent_restart_service(
    agent_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Restart the AE agent service. Privileged operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "restart_agent_service",
            "agent_id": agent_id,
            "reason": reason,
            "message": f"Would restart agent service for {agent_id}. No changes made.",
        })
    data = get_ae_client().restart_agent(agent_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "restart_agent_service",
        "agent_id": agent_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


async def agent_clear_stale_rdp_session(
    agent_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Clear a stuck RDP/desktop session on an agent. Privileged operation."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "clear_stale_rdp_session",
            "agent_id": agent_id,
            "reason": reason,
            "message": f"Would clear RDP session on agent {agent_id}. No changes made.",
        })
    data = get_ae_client().clear_agent_rdp(agent_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "clear_stale_rdp_session",
        "agent_id": agent_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ═══════════════════════════════════════════════════════════════════════
#  P1 support: list_running, get_recent_failures, get_last_heartbeat, collect_diagnostics
# ═══════════════════════════════════════════════════════════════════════

async def agent_list_running() -> str:
    """
    MANDATORY: Always call this tool first if asked about agent status or logs and the agent ID is unknown.
    Lists all agents currently in Running/Connected/Active state with their Names and IDs.
    """
    agents = get_ae_client().list_agents()
    running = [
        a for a in agents
        if (a.get("agentState") or a.get("state") or "").upper() in ("CONNECTED", "RUNNING", "ACTIVE")
    ]
    items = []
    for a in running:
        items.append({
            "agent_id": a.get("agentId") or a.get("id"),
            "agent_name": a.get("agentName") or a.get("name"),
            "state": a.get("agentState") or a.get("state")
        })
    
    return _safe_json({
        "message": f"I found {len(items)} running agents. Please pick one:",
        "agents": items,
        "options": [f"{a['agent_name']} (ID: {a['agent_id']})" for a in items],
        "instruction": "Reply with a Name or ID from the list."
    })


async def agent_get_recent_failures(agent_id: str, limit: int = 20) -> str:
    """Recent failed requests on this agent."""
    client = get_ae_client()
    failures = client.search_requests(filters={"agentName": agent_id, "status": "Failure"}, limit=limit)
    items = [{"request_id": r.get("id") or r.get("automationRequestId"), "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"), "error": r.get("errorMessage"), "created": r.get("createdDate")} for r in failures[:limit]]
    return _safe_json({"agent_id": agent_id, "recent_failures": items, "count": len(items)})


async def agent_get_last_heartbeat(agent_id: str) -> str:
    """Last seen / last heartbeat time for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next((a for a in agents if (a.get("agentId") or a.get("id")) == agent_id or (a.get("agentName") or a.get("name")) == agent_id), {})
    last = data.get("lastSeen") or data.get("lastHeartbeat") or data.get("lastUpdated")
    return _safe_json({"agent_id": agent_id, "agent_name": data.get("agentName") or data.get("name"), "last_heartbeat": last})


async def agent_collect_diagnostics(agent_id: str) -> str:
    """Gather support diagnostics for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        data = {}
    agents = get_ae_client().list_agents()
    match = next((a for a in agents if (a.get("agentId") or a.get("id")) == agent_id or (a.get("agentName") or a.get("name")) == agent_id), data)
    return _safe_json({"agent_id": agent_id, "diagnostics": match})


async def agent_analyze_logs(
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
    import gzip
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

    # 2. Parse Dates (milliseconds)
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
        return {"error": f"Invalid date format: {e}"}

    # 3. Request Logs
    logger.info("Requesting logs for agent %s (%s) from %s to %s", agent_id, agent_uuid, f_ms, t_ms)
    req_resp = client.request_agent_debug_logs(agent_uuid, f_ms, t_ms)
    req_id = req_resp.get("id")
    if not req_id:
        return {"error": "Failed to initiate log extraction", "raw": req_resp}

    # 4. Polling
    logger.info("Polling for log request %s completion...", req_id)
    max_polls = 15
    log_file_link = None
    for i in range(max_polls):
        time.sleep(5)
        status_resp = client.get_agent_debug_logs(str(req_id))
        if status_resp.get("status") == "COMPLETE":
            log_file_link = status_resp.get("logFileLink")
            break
        if status_resp.get("status") in ("FAILED", "ERROR"):
            return {"error": "Log extraction failed on server", "details": status_resp}
    
    if not log_file_link:
        return {"error": "Timed out waiting for logs to be ready", "request_id": req_id}

    # 5. Download and Extract
    logger.info("Downloading logs from %s", log_file_link)
    zip_bytes = client.get(log_file_link, use_rest=False)
    
    if not isinstance(zip_bytes, bytes):
        return {"error": "Failed to download log ZIP", "raw": str(zip_bytes)[:200]}

    log_summary = []
    error_found = False
    all_errors = []
    
    # Date regex for filenames like agent.log.2026-03-16 or agent.log.20260316
    date_pattern = re.compile(r"(\d{4}-?\d{2}-?\d{2})")

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            all_files_in_zip = z.namelist()
            logger.info("Extracting %d files from log ZIP for agent %s", len(all_files_in_zip), agent_id)

            eligible_files = [n for n in all_files_in_zip if ".log" in n or "stdout" in n or "stderr" in n]
            
            for name in eligible_files:
                # Filter by date if filename contains one
                match_dt = date_pattern.search(name)
                if match_dt:
                    try:
                        raw_date = match_dt.group(1).replace("-", "")
                        file_dt = datetime.strptime(raw_date, "%Y%m%d")
                        if file_dt.date() < (orig_f_dt.date() - timedelta(days=1)) or file_dt.date() > (orig_t_dt.date() + timedelta(days=1)):
                            continue
                    except ValueError:
                        pass

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
    except Exception as e:
        return {"error": f"Failed to process log ZIP: {e}"}

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
        if not entry.get("errors_found"): continue
        
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

    # 6. Return Result
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
