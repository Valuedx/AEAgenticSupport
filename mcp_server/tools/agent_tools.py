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
) -> dict:
    """
    Extract and analyze agent logs for a specific period.

    Uses the same backward-scan, timestamp-gated extraction as the in-app
    analyzer so that error detection quality is identical across surfaces.
    """
    import io
    import zipfile
    import time
    import re
    import gzip
    from datetime import datetime, timedelta
    from tools.agent_debug_tools import (
        _extract_errors_from_file_lines,
        _group_errors,
        _build_ai_prompt,
        _ts_in_window,
    )

    client = get_ae_client()

    # ── 0. Resolve Agent (empty-ID → discovery) ──
    agents = client.list_agents()

    if not agent_id or not agent_id.strip():
        running = [
            a for a in agents
            if (a.get("agentState") or a.get("state") or "").upper()
            in ("RUNNING", "CONNECTED", "ACTIVE")
        ]
        return {
            "success": True,
            "discovery": True,
            "running_agents": [
                {
                    "agent_id": str(a.get("agentId") or a.get("id")),
                    "agent_name": a.get("agentName") or a.get("name"),
                    "state": a.get("agentState") or a.get("state"),
                }
                for a in running
            ],
            "count": len(running),
            "message": (
                f"Found {len(running)} running agent(s). "
                "Please provide an agent_id to analyze logs."
            ),
        }

    match = next(
        (
            a for a in agents
            if str(a.get("agentId") or a.get("id")) == agent_id
            or str(a.get("agentName") or a.get("name")).lower() == agent_id.lower()
        ),
        None,
    )

    if not match:
        return {"error": f"Agent '{agent_id}' not found"}

    agent_uuid = match.get("uuid")
    if not agent_uuid:
        return {
            "success": False,
            "error": f"Agent '{agent_id}' resolved but has no UUID — cannot request logs.",
        }

    state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()

    if state not in ("RUNNING", "CONNECTED", "ACTIVE"):
        agent_name = match.get("agentName") or match.get("name") or agent_id
        return {
            "error": f"Please restart your agent {agent_name} first",
            "agent_state": state,
        }

    # ── 1. Parse Dates ──
    retention_days = 14
    min_date = datetime.now() - timedelta(days=retention_days)
    warning_retention = False

    try:
        if from_date:
            f_dt = (
                datetime.fromisoformat(from_date)
                if isinstance(from_date, str)
                else datetime.fromtimestamp(from_date / 1000)
            )
            if f_dt < min_date:
                f_dt = min_date
                warning_retention = True
        else:
            f_dt = datetime.now() - timedelta(hours=24)

        if to_date:
            t_dt = (
                datetime.fromisoformat(to_date)
                if isinstance(to_date, str)
                else datetime.fromtimestamp(to_date / 1000)
            )
            if t_dt > datetime.now():
                t_dt = datetime.now()
        else:
            t_dt = datetime.now()

        if f_dt > t_dt:
            return {
                "success": False,
                "error": (
                    f"from_date ({f_dt.isoformat()}) is after to_date ({t_dt.isoformat()}). "
                    "Please swap them."
                ),
            }

        f_ms = int(f_dt.timestamp() * 1000)
        t_ms = int(t_dt.timestamp() * 1000)
        orig_f_dt, orig_t_dt = f_dt, t_dt

        warning_span = False
        max_span = timedelta(days=5)
        if (t_dt - f_dt) > max_span:
            f_dt = t_dt - max_span
            f_ms = int(f_dt.timestamp() * 1000)
            warning_span = True
    except Exception as e:
        return {"success": False, "error": f"Invalid date format: {e}"}

    # ── 2. Request Logs ──
    logger.info("Requesting logs for agent %s (%s) %s→%s", agent_id, agent_uuid, f_ms, t_ms)
    try:
        req_resp = client.request_agent_debug_logs(agent_uuid, f_ms, t_ms)
        req_id = req_resp.get("id")
    except Exception as e:
        return {"success": False, "error": f"Failed to initiate log extraction: {e}"}

    if not req_id:
        return {"success": False, "error": "Failed to initiate log extraction", "raw": req_resp}

    # ── 3. Poll ──
    logger.info("Polling for log request %s...", req_id)
    log_file_link = None
    zip_bytes_direct = None

    for _ in range(10):
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
        return {"success": False, "error": "Timed out waiting for logs after 100s", "request_id": req_id}

    # ── 4. Download ZIP ──
    try:
        if zip_bytes_direct:
            zip_bytes = zip_bytes_direct
        else:
            zip_data = client._authorized_request("GET", log_file_link, use_rest_prefix=False)
            zip_bytes = (
                zip_data.get("log_zip_content")
                if isinstance(zip_data, dict) and zip_data.get("is_zip")
                else zip_data
            )

        if not isinstance(zip_bytes, (bytes, bytearray)):
            return {"success": False, "error": "Failed to download log ZIP (unexpected format)"}

        # ── 5. Process each file in ZIP ──
        date_pattern = re.compile(r"(\d{4}-?\d{2}-?\d{2})")
        results_by_date: dict[str, list[dict]] = {}
        total_files_read = 0
        total_error_files = 0
        all_error_blocks: list[dict] = []

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            eligible = [
                n for n in z.namelist()
                if ".log" in n.lower() or "stdout" in n.lower() or "stderr" in n.lower()
            ]
            logger.info("ZIP has %d eligible files for agent %s", len(eligible), agent_id)

            for name in eligible:
                dt_match = date_pattern.search(name)
                file_date_str = "unknown"
                if dt_match:
                    try:
                        raw_d = dt_match.group(1).replace("-", "")
                        file_dt = datetime.strptime(raw_d, "%Y%m%d")
                        file_date_str = file_dt.strftime("%Y-%m-%d")
                        if (
                            file_dt.date() < (orig_f_dt.date() - timedelta(days=1))
                            or file_dt.date() > (orig_t_dt.date() + timedelta(days=1))
                        ):
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
                    file_lines = text.splitlines()
                    total_files_read += 1
                except Exception as ef:
                    logger.warning("Could not read %s: %s", name, ef)
                    continue

                extraction = _extract_errors_from_file_lines(
                    file_lines, max_errors=10, context_lines=50, tail_fallback=tail_lines,
                )

                # Per-line time-window filter
                if extraction["had_errors"]:
                    extraction["error_blocks"] = [
                        b for b in extraction["error_blocks"]
                        if _ts_in_window(b.get("timestamp", ""), orig_f_dt, orig_t_dt)
                    ]
                    if not extraction["error_blocks"]:
                        extraction["had_errors"] = False
                        extraction["tail_lines"] = (
                            file_lines[-tail_lines:] if len(file_lines) > tail_lines else file_lines
                        )

                file_result = {
                    "filename": name,
                    "date": file_date_str,
                    "total_lines": len(file_lines),
                    "had_errors": extraction["had_errors"],
                    "error_block_count": len(extraction["error_blocks"]),
                    "error_blocks": extraction["error_blocks"],
                    "tail_lines": extraction["tail_lines"],
                }

                if extraction["had_errors"]:
                    total_error_files += 1
                    all_error_blocks.extend(extraction["error_blocks"])

                results_by_date.setdefault(file_date_str, []).append(file_result)

        # ── 6. Group errors & AI Diagnostic ──
        error_groups: list[dict] = []
        ai_diagnostic = ""

        if all_error_blocks:
            error_groups = _group_errors(all_error_blocks)
            try:
                prompt = _build_ai_prompt(all_error_blocks)
                logger.info(
                    "AI diagnostic prompt: %d chars, %d unique groups",
                    len(prompt), len(error_groups),
                )
                ai_diagnostic = llm_client.chat(
                    prompt,
                    system=(
                        "You are an expert AutomationEdge Support Engineer. "
                        "Be structured, concise, and actionable. "
                        "Always follow the exact response format requested."
                    ),
                    max_tokens=600,
                )
            except Exception as llm_err:
                logger.error("AI diagnostic failed: %s", llm_err)
                ai_diagnostic = "(AI diagnostic unavailable)"

        # ── 7. Build Report ──
        report_lines = [f"### Log Analysis for Agent: {agent_id}"]
        report_lines.append(
            f"**Period:** {f_dt.strftime('%Y-%m-%d %H:%M:%S')} → "
            f"{t_dt.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        report_lines.append(
            f"**Files Analyzed:** {total_files_read} | "
            f"**Files with Errors:** {total_error_files} | "
            f"**Unique Error Types:** {len(error_groups)}"
        )

        if warning_retention or warning_span:
            report_lines.append("\n> [!IMPORTANT]")
            if warning_retention:
                report_lines.append(
                    f"> Start date adjusted to {f_dt.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(14-day retention limit)."
                )
            if warning_span:
                report_lines.append(
                    f"> Period capped to 5 days "
                    f"({f_dt.strftime('%Y-%m-%d')} → {t_dt.strftime('%Y-%m-%d')})."
                )

        if ai_diagnostic:
            report_lines.append(f"\n### 🤖 AI Diagnostic & Summary\n{ai_diagnostic}")

        if error_groups:
            report_lines.append("\n### 📊 Error Group Overview")
            for g in error_groups:
                threads = ", ".join(g["affected_threads"]) or "unknown"
                report_lines.append(
                    f"- **{g['signature'][:80]}** — "
                    f"{g['occurrence_count']}x | "
                    f"{g['first_seen'][:19]} → {g['last_seen'][:19]} | "
                    f"Thread(s): `{threads}`"
                )

        if not results_by_date:
            report_lines.append(
                "\n> [!WARNING]\n> No log files matched the requested date range."
            )
        else:
            report_lines.append("\n### 📂 File Details (Newest First)")
            for date_str in sorted(results_by_date.keys(), reverse=True):
                report_lines.append(f"\n---\n#### 📅 {date_str}")
                for file_res in results_by_date[date_str]:
                    fname = file_res["filename"]
                    n_blocks = file_res["error_block_count"]

                    if not file_res["had_errors"]:
                        tail_preview = file_res["tail_lines"]
                        report_lines.append(
                            f"\n✅ **{fname}** — No errors detected "
                            f"({file_res['total_lines']} lines). "
                            f"Last {len(tail_preview)} lines:"
                        )
                        report_lines.append(
                            f"```log\n{chr(10).join(tail_preview[-10:])}\n```"
                        )
                    else:
                        report_lines.append(
                            f"\n⚠️ **{fname}** — {n_blocks} error block(s):"
                        )
                        for block in file_res["error_blocks"]:
                            report_lines.append(
                                f"\n> **{block['error_label']}** | "
                                f"`{block['timestamp']}` | "
                                f"Thread: `{block['thread']}`\n"
                                f"> 💬 _{block['error_message']}_"
                            )
                            preview = block["lines"][:10]
                            report_lines.append(
                                f"```log\n{chr(10).join(preview)}\n"
                                f"... ({len(block['lines'])} lines total)\n```"
                            )

        # Keyword-based suggestions (complements AI diagnostic)
        suggested_solutions = []
        if all_error_blocks:
            all_msgs = " ".join(
                b["error_message"] + " " + b["trigger_line"] for b in all_error_blocks
            ).upper()
            if "UNKNOWNHOSTEXCEPTION" in all_msgs or "NO SUCH HOST" in all_msgs:
                suggested_solutions.append(
                    "DNS/Network: Agent cannot resolve the AE server hostname. "
                    "Check DNS settings, VPN status, or the hosts file."
                )
            if "CONNECTION RESET" in all_msgs or "UNREACHABLE" in all_msgs:
                suggested_solutions.append(
                    "Network: Agent connection is being reset/dropped. "
                    "Check firewall rules, proxy settings, and AE server health."
                )
            if "TIMEOUT" in all_msgs:
                suggested_solutions.append(
                    "Timeout: Increase timeout settings or check server/target application load."
                )
            if "CREDENTIAL" in all_msgs or "AUTHENTICATION" in all_msgs or " 401 " in all_msgs:
                suggested_solutions.append(
                    "Credentials: Update agent credentials or refresh the session in AE console."
                )
            if "OUTOFMEMORY" in all_msgs or "HEAP" in all_msgs:
                suggested_solutions.append(
                    "Memory: Increase Java Heap (-Xmx) in AEAgent.bat / AEAgent.conf."
                )
            if suggested_solutions:
                report_lines.append("\n### 💡 Suggested Solutions")
                for sol in suggested_solutions:
                    report_lines.append(f"- {sol}")

        full_report = "\n".join(report_lines)
        error_found = total_error_files > 0

        return {
            "success": True,
            "agent_id": agent_id,
            "period": f"{f_dt.date()} to {t_dt.date()}",
            "files_analyzed": total_files_read,
            "error_files": total_error_files,
            "error_found": error_found,
            "unique_error_types": len(error_groups),
            "error_groups": [
                {
                    "signature": g["signature"],
                    "occurrence_count": g["occurrence_count"],
                    "first_seen": g["first_seen"],
                    "last_seen": g["last_seen"],
                    "affected_threads": g["affected_threads"],
                }
                for g in error_groups
            ],
            "results_by_date": results_by_date,
            "report": full_report,
            "suggested_solutions": suggested_solutions,
            "summary": (
                f"Analyzed {total_files_read} file(s). "
                + (
                    f"{total_error_files} file(s) had errors across "
                    f"{len(error_groups)} unique error type(s)."
                    if error_found
                    else "No errors detected."
                )
            ),
        }

    except Exception as e:
        logger.exception("Failed to process log ZIP")
        return {"success": False, "error": f"Failed to process log ZIP: {e}"}
