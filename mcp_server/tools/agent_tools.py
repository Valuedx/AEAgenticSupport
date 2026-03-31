"""
P0 agent_read (9) + agent_mutate (2) tools — 11 total.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp_server.ae_client import get_ae_client
from config.llm_client import llm_client

logger = logging.getLogger("ae_mcp.tools.agent")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


AGENT_RESTART_SOP = """\
## ⚠️ Agent Restart Cannot Be Done Automatically

The AutomationEdge platform does **not** support remote agent restarts via API.
The agent service must be restarted **manually on the agent's Windows machine**.

---

### 📄 SOP: Restart AE Agent (Windows)

**Prerequisites:** Admin access to the agent machine · Agent installed at `D:\\PS\\T4\\ae-agent\\bin`

---

#### Option A — Command Prompt (Recommended)
1. Open **Command Prompt as Administrator** on the agent machine
2. Run:
   ```
   cd D:\\PS\\T4\\ae-agent\\bin
   shutdown.bat
   startup.bat
   ```

#### Option B — Debug Mode (Troubleshooting)
```
startup-debug.bat
```

#### Option C — Windows Service
   - Open `services.msc`
   - Locate **AutomationEdge Agent**
   - Click **Restart**

Or via CMD:
```
aeagent-service.exe stop
aeagent-service.exe start
```

---

### ✅ Verify After Restart
1. Open [https://t4.automationedge.com/](https://t4.automationedge.com/)
2. Go to **Manage → Agents**
3. Confirm agent status = **Online** and the correct machine name is visible

---

### 🔧 Common Issues

| Problem | Fix |
|---------|-----|
| Agent not starting | Run CMD as **Administrator** |
| "Already running" error | Delete `aeagent.pid` in the `bin` folder |
| Not visible in portal | Check network / firewall to the AE server |
| Port conflict | Verify port availability |
"""


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
    return {
        "success": True,
        "stopped_agents": items,
        "count": len(items)
    }


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
    return {
        "success": True,
        "unknown_agents": items,
        "count": len(items)
    }


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
            return {"success": False, "error": f"Agent '{agent_id}' not found"}
        state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
        return {
            "success": True,
            "agent_id": match.get("agentId") or match.get("id"),
            "agent_name": match.get("agentName") or match.get("name"),
            "state": state,
            "is_healthy": state in ("CONNECTED", "RUNNING", "ACTIVE"),
            "last_heartbeat": match.get("lastSeen") or match.get("lastHeartbeat"),
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
    if isinstance(data, dict) and "error" in data:
        return {"success": False, **data}
    return {
        "success": True,
        "agent_id": agent_id,
        "details": data
    }


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
        return {
            "success": True,
            "agent_id": agent_id,
            "agent_name": match.get("agentName") or match.get("name"),
            "state": match.get("agentState") or match.get("state"),
            "running_requests": running_count,
            "max_concurrent": match.get("maxConcurrent") or match.get("concurrency"),
            "cpu_usage": match.get("cpuUsage"),
            "memory_usage": match.get("memoryUsage"),
        }
    return {
        "success": True,
        "agent_id": agent_id,
        "running_requests": running_count
    }


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
    return {
        "success": True,
        "agent_id": agent_id,
        "running_requests": items,
        "count": len(items)
    }


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

    return {
        "success": True,
        "agent_id": agent_id,
        "assigned_workflows": items,
        "count": len(items)
    }


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
    return {
        "success": True,
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "state": state,
        "connected_to_controller": state in ("CONNECTED", "RUNNING", "ACTIVE"),
        "controller": data.get("controllerName") or data.get("controller"),
        "controller_state": data.get("controllerState"),
        "platform_reachable": data.get("platformReachable"),
        "last_heartbeat": data.get("lastSeen") or data.get("lastHeartbeat"),
    }


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

    return {
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "rdp_session_active": data.get("rdpSessionActive") or data.get("desktopSessionActive"),
        "rdp_user": data.get("rdpUser") or data.get("desktopUser"),
        "screen_resolution": data.get("screenResolution"),
        "locked": data.get("screenLocked") or data.get("isLocked"),
    }


# ═══════════════════════════════════════════════════════════════════════
#  agent_mutate
# ═══════════════════════════════════════════════════════════════════════

AGENT_RESTART_SOP = """\
## ⚠️ Agent Restart Cannot Be Done Automatically

The AutomationEdge platform does **not** support remote agent restarts via API.
The agent service must be restarted **manually on the agent's Windows machine**.

---

### 📄 SOP: Restart AE Agent (Windows)

**Prerequisites:** Admin access to the agent machine · Agent installed at `D:\\PS\\T4\\ae-agent\\bin`

---

#### Option A — Command Prompt (Recommended)
1. Open **Command Prompt as Administrator** on the agent machine
2. Run:
   ```
   cd D:\\PS\\T4\\ae-agent\\bin
   shutdown.bat
   startup.bat
   ```

#### Option B — Debug Mode (Troubleshooting)
```
startup-debug.bat
```

#### Option C — Windows Service
   - Open `services.msc`
   - Locate **AutomationEdge Agent**
   - Click **Restart**

Or via CMD:
```
aeagent-service.exe stop
aeagent-service.exe start
```

---

### ✅ Verify After Restart
1. Open [https://t4.automationedge.com/](https://t4.automationedge.com/)
2. Go to **Manage → Agents**
3. Confirm agent status = **Online** and the correct machine name is visible

---

### 🔧 Common Issues

| Problem | Fix |
|---------|-----|
| Agent not starting | Run CMD as **Administrator** |
| "Already running" error | Delete `aeagent.pid` in the `bin` folder |
| Not visible in portal | Check network / firewall to the AE server |
| Port conflict | Verify port availability |
| No logs | Use `startup-debug.bat` |
"""


async def agent_restart_service(
    agent_id: str,
) -> dict:
    """
    Agent restart cannot be performed remotely via the API.
    Returns the manual SOP so the user can restart their agent locally.
    """
    return {
        "success": False,
        "automated_restart": "not_supported",
        "agent_id": agent_id,
        "message": (
            "Agent restart is not available via the API. "
            "Please follow the manual SOP below to restart the agent on its Windows machine."
        ),
        "sop": AGENT_RESTART_SOP,
    }




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
    Lists ONLY agents currently in Running/Connected/Active state.
    Use this ONLY when the user wants to pick an agent to work on (e.g. logs, diagnostics).
    If the user asks for 'agent status', 'show all agents', or 'which agents are online/offline',
    use ae.agent.list_all instead — it shows every agent with their state.
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

    return {
        "success": True,
        "message": f"I found {len(items)} running agents. Please pick one:",
        "agents": items,
        "options": [f"{a['agent_name']} (ID: {a['agent_id']})" for a in items],
        "instruction": "Reply with a Name or ID from the list."
    }


async def agent_list_all() -> dict:
    """
    Use this when the user asks for 'agent status', 'show all agents', 'how many agents',
    'which agents are running/offline/stopped', or any general agent overview.
    Returns ALL agents grouped by state (Running, Stopped, Offline, Unknown).
    """
    agents = get_ae_client().list_agents()

    _RUNNING = ("CONNECTED", "RUNNING", "ACTIVE")
    _STOPPED = ("STOPPED", "DISCONNECTED", "INACTIVE")
    _OFFLINE = ("OFFLINE", "UNREACHABLE", "LOST")

    groups: dict[str, list] = {"running": [], "stopped": [], "offline": [], "other": []}

    for a in agents:
        raw_state = (a.get("agentState") or a.get("state") or "UNKNOWN").upper()
        entry = {
            "agent_id": a.get("agentId") or a.get("id"),
            "agent_name": a.get("agentName") or a.get("name"),
            "host": a.get("hostName") or a.get("host") or "",
            "state": raw_state,
            "last_seen": a.get("lastSeen") or a.get("lastHeartbeat") or "",
        }
        if raw_state in _RUNNING:
            groups["running"].append(entry)
        elif raw_state in _STOPPED:
            groups["stopped"].append(entry)
        elif raw_state in _OFFLINE:
            groups["offline"].append(entry)
        else:
            groups["other"].append(entry)

    # Build Markdown Report
    report = ["## 🖥️ Agent Status Overview"]
    report.append(f"Found **{len(agents)}** agents in total.\n")
    
    if groups["running"]:
        report.append("### 🟢 Running Agents")
        for a in groups["running"]:
            host = f" ({a['host']})" if a['host'] else ""
            report.append(f"- **{a['agent_name']}**{host} [ID: `{a['agent_id']}`]")
    
    if groups["stopped"]:
        report.append("\n### 🟡 Stopped Agents")
        for a in groups["stopped"]:
            host = f" ({a['host']})" if a['host'] else ""
            report.append(f"- **{a['agent_name']}**{host} [ID: `{a['agent_id']}`] — State: {a['state']}")
            
    if groups["offline"]:
        report.append("\n### 🔴 Offline/Lost Agents")
        for a in groups["offline"]:
            host = f" ({a['host']})" if a['host'] else ""
            report.append(f"- **{a['agent_name']}**{host} [ID: `{a['agent_id']}`] — Last seen: {a['last_seen']}")

    total = len(agents)
    return {
        "success": True,
        "total_agents": total,
        "report": "\n".join(report),
        "summary": {
            "running": len(groups["running"]),
            "stopped": len(groups["stopped"]),
            "offline": len(groups["offline"]),
            "other": len(groups["other"]),
        },
        "agents_by_state": groups,
    }



async def agent_get_recent_failures(agent_id: str, limit: int = 20) -> str:
    """Recent failed requests on this agent."""
    client = get_ae_client()
    failures = client.search_requests(filters={"agentName": agent_id, "status": "Failure"}, limit=limit)
    items = [{"request_id": r.get("id") or r.get("automationRequestId"), "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"), "error": r.get("errorMessage"), "created": r.get("createdDate")} for r in failures[:limit]]
    return {
        "success": True,
        "agent_id": agent_id,
        "recent_failures": items,
        "count": len(items)
    }


async def agent_get_last_heartbeat(agent_id: str) -> str:
    """Last seen / last heartbeat time for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        agents = get_ae_client().list_agents()
        data = next((a for a in agents if (a.get("agentId") or a.get("id")) == agent_id or (a.get("agentName") or a.get("name")) == agent_id), {})
    last = data.get("lastSeen") or data.get("lastHeartbeat") or data.get("lastUpdated")
    return {
        "success": True,
        "agent_id": agent_id,
        "agent_name": data.get("agentName") or data.get("name"),
        "last_heartbeat": last
    }


async def agent_collect_diagnostics(agent_id: str) -> str:
    """Gather support diagnostics for an agent."""
    try:
        data = get_ae_client().get_agent(agent_id)
    except Exception:
        data = {}
    agents = get_ae_client().list_agents()
    match = next((a for a in agents if (a.get("agentId") or a.get("id")) == agent_id or (a.get("agentName") or a.get("name")) == agent_id), data)
    stats = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
    return {
        "success": True,
        "agent_id": agent_id,
        "agent_state": stats,
        "diagnostics": match
    }


async def agent_analyze_logs(
    agent_id: str,
    from_date: str | int = "",
    to_date: str | int = "",
    tail_lines: int = 100,
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
    
    # 0. Resolve Agent — list_agents() only returns CONNECTED agents.
    # Stopped/offline agents won't appear, so we fall back to get_agent() by ID or name.
    agents = client.list_agents()
    match = next(
        (a for a in agents 
         if str(a.get("agentId") or a.get("id")) == agent_id 
         or str(a.get("agentName") or a.get("name")).lower() == agent_id.lower()), 
        None
    )

    if not match:
        # Fallback: try direct lookup (finds stopped/offline agents too)
        try:
            match = client.get_agent(agent_id)
        except Exception:
            pass

    # 1. Check Status
    if not match:
        return {
            "success": False,
            "error": f"Agent '{agent_id}' not found. Use ae.agent.list_running to see available agents.",
        }

    agent_uuid = match.get("uuid")
    agent_name = match.get("agentName") or match.get("name") or agent_id
    state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()

    if state not in ("RUNNING", "CONNECTED", "ACTIVE"):
        agent_host = match.get("hostName") or match.get("host") or ""
        display = f"{agent_name} ({agent_host})" if agent_host else agent_name
        return {
            "success": False,
            "error": (
                f"Cannot retrieve logs: agent **{display}** is currently **{state}**. "
                f"The physical agent service must be RUNNING to extract logs."
            ),
            "agent_name": agent_name,
            "agent_state": state,
            "action_required": "Please restart the agent service on the agent machine.",
            "sop": AGENT_RESTART_SOP,
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
        return {"success": False, "error": f"Invalid date format: {e}"}

    # 3. Request Logs
    logger.info("Requesting logs for agent %s (%s) from %s to %s", agent_name, agent_uuid, f_ms, t_ms)
    try:
        req_resp = client.request_agent_debug_logs(agent_uuid, f_ms, t_ms)
    except Exception as e:
        # AE-1708 handling
        err_msg = str(e)
        if "AE-1708" in err_msg or "Agent is not running" in err_msg:
            return {
                "success": False,
                "error": f"The AutomationEdge platform reports: **Agent '{agent_name}' is not running (AE-1708)**. Extraction is not possible in this state.",
                "agent_name": agent_name,
                "sop": AGENT_RESTART_SOP
            }
        return {"success": False, "error": f"Failed to initiate log extraction for {agent_name}: {e}"}

    req_id = req_resp.get("id")
    if not req_id:
        return {"success": False, "error": f"Failed to initiate log extraction for {agent_name}", "raw": req_resp}

    # 4. Polling
    # Prefer the specific request endpoint, but fall back to the list endpoint because
    # some T4 deployments surface completion status only in GET /agent/debuglogs.
    logger.info("Polling for log request %s completion...", req_id)
    poll_interval_seconds = max(1, int(os.getenv("AE_AGENT_LOG_POLL_INTERVAL_SECONDS", "5")))
    max_wait_seconds = max(poll_interval_seconds, int(os.getenv("AE_AGENT_LOG_MAX_WAIT_SECONDS", "180")))
    max_polls = max(1, (max_wait_seconds + poll_interval_seconds - 1) // poll_interval_seconds)
    log_file_name = None
    last_status = "NEW"
    for i in range(max_polls):
        time.sleep(poll_interval_seconds)
        status_resp = None
        try:
            status_resp = client.get_agent_debug_logs(str(req_id))
        except Exception as poll_err:
            logger.warning("Poll attempt %d failed: %s", i, poll_err)
        # Fallback: list endpoint is often more reliable than /agent/debuglogs/{id}
        specific_status = status_resp.get("status", "") if isinstance(status_resp, dict) else ""
        if (not isinstance(status_resp, dict)) or specific_status not in ("COMPLETE", "COMPLETED", "FAILED", "ERROR"):
            try:
                list_resp = client.get_agent_debug_logs()
                if isinstance(list_resp, list):
                    list_match = next(
                        (
                            e for e in list_resp
                            if str(e.get("id")) == str(req_id)
                        ),
                        None,
                    )
                    if isinstance(list_match, dict):
                        status_resp = list_match
            except Exception as list_err:
                logger.warning("Poll list fallback attempt %d failed: %s", i, list_err)
        # get_agent_debug_logs may return a list (all entries) or a dict (single entry)
        if isinstance(status_resp, list):
            status_resp = next(
                (e for e in status_resp if str(e.get("id")) == str(req_id)), {}
            )
        if not isinstance(status_resp, dict):
            continue
        status = status_resp.get("status", "")
        last_status = status or last_status
        if status in ("COMPLETE", "COMPLETED"):
            log_file_name = status_resp.get("logFileLink")
            break
        if status in ("FAILED", "ERROR"):
            return {"success": False, "error": "Log extraction failed on server", "details": status_resp}

    if not log_file_name:
        return {
            "success": False,
            "error": "Timed out waiting for logs to be ready",
            "request_id": req_id,
            "agent_name": agent_name,
            "agent_state": state,
            "last_status": last_status,
            "waited_seconds": max_wait_seconds,
        }

    # 5. Download and Extract
    # T4 commonly serves the ZIP directly from GET /agent/debuglogs/{id}.
    # Keep the old /download?id= path only as a fallback for older variants.
    download_candidates = [
        f"/agent/debuglogs/{req_id}",
        f"/agent/debuglogs/download?id={req_id}",
    ]
    zip_bytes = None
    download_path = download_candidates[0]
    last_download_error = ""
    for candidate in download_candidates:
        try:
            logger.info("Downloading logs via %s (file: %s)", candidate, log_file_name)
            zip_bytes = client.get(candidate, use_rest=True)
            download_path = candidate
            if isinstance(zip_bytes, bytes):
                break
        except Exception as download_err:
            last_download_error = str(download_err)
            logger.warning("Download attempt failed via %s: %s", candidate, download_err)

    if not isinstance(zip_bytes, bytes):
        return {
            "success": False,
            "error": "Failed to download log ZIP",
            "request_id": req_id,
            "agent_name": agent_name,
            "agent_state": state,
            "last_status": last_status,
            "download_attempts": download_candidates,
            "raw": str(zip_bytes)[:200] if zip_bytes is not None else last_download_error[:200],
        }

    log_summary = []
    error_found = False
    all_errors = []
    zip_member_names = []
    
    # Date regex for filenames like agent.log.2026-03-16 or agent.log.20260316,
    # and for timestamps embedded in log lines.
    date_pattern = re.compile(r"(\d{4}-?\d{2}-?\d{2})")

    def _normalize_date_token(raw_date: str) -> str:
        compact = raw_date.replace("-", "")
        try:
            return datetime.strptime(compact, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return raw_date

    def _extract_date_from_text(text: str) -> date | None:
        match_dt = date_pattern.search(text or "")
        if not match_dt:
            return None
        try:
            return datetime.strptime(
                match_dt.group(1).replace("-", ""),
                "%Y%m%d",
            ).date()
        except ValueError:
            return None

    def _is_current_live_agent_log(filename: str) -> bool:
        base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        return base in {"aeagent", "agent", "aeagent.txt", "agent.txt"}

    def _extract_entry_date(entry: dict[str, Any]) -> str:
        raw_lines = entry.get("full_lines", [])
        error_lines = [
            line for line in raw_lines
            if any(token in line.upper() for token in ("ERROR", "FATAL", "EXCEPTION"))
        ]

        for candidate_line in error_lines + raw_lines:
            match_dt = date_pattern.search(candidate_line)
            if match_dt:
                return _normalize_date_token(match_dt.group(1))

        if _is_current_live_agent_log(entry["filename"]):
            return t_dt.strftime("%Y-%m-%d")

        match_dt = date_pattern.search(entry["filename"])
        if match_dt:
            return _normalize_date_token(match_dt.group(1))

        return "Unknown Date"

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            all_files_in_zip = z.namelist()
            zip_member_names = list(all_files_in_zip)
            logger.info("Extracting %d files from log ZIP for agent %s", len(all_files_in_zip), agent_id)

            def _is_agent_log_member(name: str) -> bool:
                lowered = name.lower()
                base = lowered.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if ".log" in lowered or "stdout" in lowered or "stderr" in lowered:
                    return True
                # Current live AE agent log may be named `aeagent`, `aeagent.txt`,
                # or similar without a dated suffix.
                if _is_current_live_agent_log(base):
                    return True
                return False

            eligible_files = [n for n in all_files_in_zip if _is_agent_log_member(n)]
            
            for name in eligible_files:
                # Filter by date if filename contains one
                basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                match_dt = date_pattern.search(basename)
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
                    if _is_current_live_agent_log(name):
                        has_dated_lines = any(_extract_date_from_text(line) for line in lines)
                        if has_dated_lines:
                            filtered_lines = []
                            keep_segment = False
                            for line in lines:
                                line_date = _extract_date_from_text(line)
                                if line_date is not None:
                                    keep_segment = orig_f_dt.date() <= line_date <= orig_t_dt.date()
                                if keep_segment:
                                    filtered_lines.append(line)
                            lines = filtered_lines
                            if not lines:
                                continue
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
        return {"success": False, "error": f"Failed to process log ZIP: {e}"}

    # Generate report
    report_lines = [f"### Log Analysis for Agent: {agent_name}"]
    report_lines.append(f"**Agent ID:** `{match.get('agentId') or match.get('id') or agent_id}`")
    report_lines.append(f"**Agent State:** {state}")
    report_lines.append(f"**Log Request ID:** `{req_id}`")
    report_lines.append(f"**Period:** {f_dt.strftime('%Y-%m-%d %H:%M:%S')} to {t_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"**Server Extraction Status:** {last_status}")
    report_lines.append(f"**ZIP File:** `{log_file_name}`")
    report_lines.append(f"**Download Endpoint:** `{download_path}`")
    
    if warning_retention or warning_span:
        report_lines.append("\n> [!IMPORTANT]")
        if warning_retention:
            report_lines.append(f"> Requested start date was adjusted to {f_dt.strftime('%Y-%m-%d %H:%M:%S')} due to AutomationEdge's 14-day log retention policy.")
        if warning_span:
            report_lines.append(f"> The requested period was capped to 5 days ({f_dt.strftime('%Y-%m-%d %H:%M:%S')} to {t_dt.strftime('%Y-%m-%d %H:%M:%S')}) due to AutomationEdge platform limits.")
    
    report_lines.append(f"**Files Analyzed:** {len(log_summary)}")
    
    # Group all analyzed logs by date so the report can show both
    # issue dates and clean dates.
    by_date = {}
    for entry in log_summary:
        # Prefer timestamps from log lines so current files like `aeagent.log`
        # or `aeagent` do not collapse into "Unknown Date".
        dt_str = _extract_entry_date(entry)

        if dt_str not in by_date:
            by_date[dt_str] = {
                "files_analyzed": 0,
                "total_errors": 0,
                "items": [],
            }

        by_date[dt_str]["files_analyzed"] += 1
        by_date[dt_str]["total_errors"] += int(entry.get("errors_found", 0))

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

        by_date[dt_str]["items"].append({
            "file": entry["filename"],
            "count": entry["errors_found"],
            "snippets": unique_snippets,
            "tail_preview": raw_lines[-10:],
        })

    # Generate AI Diagnostic Summary
    # Keep this opt-in so external LLM latency or credential issues never block
    # the core log extraction/reporting flow.
    ai_diagnostic = ""
    ai_summary_enabled = str(os.getenv("AE_AGENT_LOG_AI_SUMMARY_ENABLED", "false")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    clean_tail_summary_enabled = str(
        os.getenv("AE_AGENT_LOG_CLEAN_TAIL_SUMMARY_ENABLED", "true")
    ).strip().lower() in {"1", "true", "yes", "on"}

    def _summarize_clean_tail(item: dict[str, Any]) -> str:
        if item.get("tail_summary"):
            return str(item["tail_summary"])

        preview = "\n".join(item.get("tail_preview") or []).strip()
        if not preview:
            item["tail_summary"] = "The latest log tail is quiet and does not show any immediate health concerns."
            return str(item["tail_summary"])

        if not clean_tail_summary_enabled:
            item["tail_summary"] = "The latest 10 log lines look healthy and do not show any immediate issues."
            return str(item["tail_summary"])

        try:
            prompt = (
                "Summarize these last 10 AutomationEdge agent log lines in one short sentence. "
                "Focus on healthy signals only. Do not invent issues.\n\n"
                f"Log tail:\n{preview}"
            )
            summary = str(
                llm_client.chat(
                    prompt,
                    system=(
                        "You are an expert AutomationEdge support engineer. "
                        "Write one concise health summary sentence."
                    ),
                    max_tokens=80,
                )
                or ""
            ).strip()
            if summary:
                item["tail_summary"] = summary
                return summary
        except Exception as llm_err:
            logger.warning("Failed to generate clean tail summary for %s: %s", item.get("file"), llm_err)

        item["tail_summary"] = "The latest 10 log lines look healthy and do not show any immediate issues."
        return str(item["tail_summary"])

    if error_found and ai_summary_enabled:
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
    elif error_found:
        logger.info("Skipping AI diagnostic summary for agent log analysis because AE_AGENT_LOG_AI_SUMMARY_ENABLED is false")

    if not log_summary:
        report_lines.append("\n> [!WARNING]")
        report_lines.append(f"> **No log files were found for agent '{agent_name}' in the requested period ({f_dt.date()} to {t_dt.date()}).**")
        report_lines.append("> This often happens if the agent machine was powered off, the service was stopped, or log files were rotated/deleted locally.")
        status_msg = f"Logs for agent {agent_name} on {f_dt.date()} are not available."
    elif not error_found:
        report_lines.append("\n### ✅ Clean Trace")
        report_lines.append(f"No critical errors (ERROR, FATAL, EXCEPTION) were detected in the analyzed logs for **{agent_name}**.")
        report_lines.append("\n### 📅 Date-wise Log Review")
        for dt_str in sorted(by_date.keys(), reverse=True):
            date_info = by_date[dt_str]
            report_lines.append(f"\n#### 📅 {dt_str}")
            report_lines.append(f"- ✅ No issues detected in {date_info['files_analyzed']} file(s) checked for this date.")
            report_lines.append("- Good health summary from the last 10 log lines:")
            for item in date_info["items"]:
                report_lines.append(
                    f"- **{item['file']}** summary: {_summarize_clean_tail(item)}"
                )
        status_msg = f"Log analysis for {agent_name} completed. Status: HEALTHY."
    else:
        if ai_diagnostic:
            report_lines.append("\n### 🤖 AI Diagnostic Summary")
            report_lines.append(ai_diagnostic)

        report_lines.append("\n### 📅 Date-wise Log Review")
        for dt_str in sorted(by_date.keys(), reverse=True):
            date_info = by_date[dt_str]
            report_lines.append(f"\n#### 📅 {dt_str}")
            if date_info["total_errors"] > 0:
                report_lines.append(
                    f"- ⚠️ Issues detected in {date_info['files_analyzed']} file(s) for this date "
                    f"({date_info['total_errors']} errors total)."
                )
            else:
                report_lines.append(f"- ✅ No issues detected in {date_info['files_analyzed']} file(s) checked for this date.")
                report_lines.append("- Good health summary from the last 10 log lines:")
                for item in date_info["items"]:
                    report_lines.append(
                        f"- **{item['file']}** summary: {_summarize_clean_tail(item)}"
                    )
                continue

            for item in date_info["items"]:
                if not item["count"]:
                    continue
                report_lines.append(f"- **{item['file']}** ({item['count']} errors):")
                for snip in item["snippets"]:
                    # Use code block for snippets
                    report_lines.append(f"```log\n{snip}\n```")
        
        status_msg = f"Found {len(all_errors)} errors across {len([l for l in log_summary if l['errors_found'] > 0])} files."

    # Generate suggested actions based on log keywords
    suggested_solutions = []
    if error_found:
        error_text = "\n".join(all_errors).upper()
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
        "agent_name": agent_name,
        "agent_state": state,
        "request_id": req_id,
        "zip_file_name": log_file_name,
        "zip_download_endpoint": download_path,
        "zip_members": zip_member_names,
        "period": f"{f_dt.date()} to {t_dt.date()}",
        "error_found": error_found,
        "logs": log_summary,
        "report": full_report,
        "summary": f"Analyzed {len(log_summary)} files. {'Issues detected.' if error_found else 'No issues found.'}",
        "suggested_solutions": suggested_solutions,
        "message": status_msg
    }
