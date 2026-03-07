"""
P0 support_composite tools — 8 one-shot diagnostic tools.

These aggregate multiple API calls into a single diagnostic report
for common support scenarios.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.support_composite")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ── ae.support.diagnose_failed_request ────────────────────────────────

async def diagnose_failed_request(request_id: str) -> str:
    """One-shot diagnosis of a failed request. Gathers request details,
    error info, step logs, and failure classification."""
    client = get_ae_client()

    req = client.get_request(request_id)
    wf_config = req.get("workflowConfiguration") or {}
    status = req.get("status", "UNKNOWN")
    error = req.get("errorMessage") or req.get("errorDetails") or ""

    error_step = None
    last_success_step = None
    try:
        steps = client.get_request_steps(request_id)
        if isinstance(steps, list):
            for s in steps:
                if not isinstance(s, dict):
                    continue
                st = (s.get("status") or "").upper()
                name = s.get("stepName") or s.get("name")
                if st in ("FAILURE", "ERROR", "FAILED"):
                    error_step = {
                        "step_name": name,
                        "error": s.get("errorMessage") or s.get("error"),
                    }
                elif st in ("COMPLETE", "COMPLETED", "SUCCESS"):
                    last_success_step = name
    except Exception:
        pass

    err_lower = error.lower()
    category = "UNKNOWN"
    if "credential" in err_lower or "pool" in err_lower:
        category = "CREDENTIAL_ISSUE"
    elif "timeout" in err_lower:
        category = "TIMEOUT"
    elif "connection" in err_lower or "network" in err_lower:
        category = "CONNECTIVITY"
    elif "permission" in err_lower or "access" in err_lower:
        category = "PERMISSION_DENIED"
    elif "file" in err_lower or "path" in err_lower:
        category = "FILE_SYSTEM"
    elif "input" in err_lower or "parameter" in err_lower:
        category = "INPUT_VALIDATION"
    elif error:
        category = "APPLICATION_ERROR"

    recommendations: list[str] = []
    if category == "CREDENTIAL_ISSUE":
        recommendations.append("Check credential pool availability using ae.credential_pool.get_availability.")
    elif category == "TIMEOUT":
        recommendations.append("Review workflow timeout settings. Consider increasing timeout or checking target system responsiveness.")
    elif category == "CONNECTIVITY":
        recommendations.append("Verify network connectivity from agent to target system. Check agent status.")
    elif category == "PERMISSION_DENIED":
        recommendations.append("Review workflow and credential permissions.")
    elif category == "FILE_SYSTEM":
        recommendations.append("Check file paths and agent filesystem access.")
    elif category == "INPUT_VALIDATION":
        recommendations.append("Review input parameters using ae.request.get_input_parameters.")
    else:
        recommendations.append("Review step logs for detailed error context.")

    return _safe_json({
        "request_id": request_id,
        "status": status,
        "workflow_name": req.get("workflowName") or wf_config.get("name"),
        "agent_name": req.get("agentName"),
        "error_message": error,
        "failure_category": category,
        "failing_step": error_step,
        "last_successful_step": last_success_step,
        "submitted_by": req.get("userId"),
        "created": req.get("createdDate"),
        "completed": req.get("completedDate"),
        "recommendations": recommendations,
    })


# ── ae.support.diagnose_stuck_running_request ─────────────────────────

async def diagnose_stuck_running_request(request_id: str) -> str:
    """Diagnose a request that appears to be hung in Running state."""
    client = get_ae_client()

    req = client.get_request(request_id)
    status = req.get("status", "UNKNOWN")
    agent_name = req.get("agentName")
    created = req.get("createdDate") or req.get("pickedDate")

    findings: list[str] = []
    if status != "Running":
        findings.append(f"Request is in '{status}' state, not Running.")

    from datetime import datetime, timezone
    if isinstance(created, (int, float)):
        age_min = (datetime.now(timezone.utc).timestamp() * 1000 - created) / 60000
        findings.append(f"Request has been running for {age_min:.0f} minutes.")

    active_step = None
    try:
        steps = client.get_request_steps(request_id)
        if isinstance(steps, list):
            for s in steps:
                if isinstance(s, dict):
                    st = (s.get("status") or "").upper()
                    if st in ("RUNNING", "EXECUTING", "IN_PROGRESS"):
                        active_step = s.get("stepName") or s.get("name")
            if active_step:
                findings.append(f"Currently executing step: {active_step}")
            else:
                findings.append("No step appears to be actively running — possible hang between steps.")
    except Exception:
        findings.append("Could not retrieve step information.")

    if agent_name:
        try:
            agents = client.list_agents()
            match = next(
                (a for a in agents if (a.get("agentName") or a.get("name")) == agent_name),
                None,
            )
            if match:
                state = (match.get("agentState") or match.get("state") or "UNKNOWN").upper()
                if state not in ("CONNECTED", "RUNNING", "ACTIVE"):
                    findings.append(f"Agent '{agent_name}' is in {state} state — agent issue may be the cause.")
                else:
                    findings.append(f"Agent '{agent_name}' is healthy ({state}).")
            else:
                findings.append(f"Agent '{agent_name}' not found in monitoring data.")
        except Exception:
            pass

    recommendations = []
    if "hang between steps" in " ".join(findings).lower():
        recommendations.append("Consider terminating and restarting the request.")
    if any("agent" in f.lower() and ("stopped" in f.lower() or "disconnect" in f.lower()) for f in findings):
        recommendations.append("Agent may need restart. Use ae.agent.restart_service.")
    recommendations.append("Use ae.request.terminate_running if request needs to be killed.")

    return _safe_json({
        "request_id": request_id,
        "status": status,
        "workflow_name": req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name"),
        "agent_name": agent_name,
        "active_step": active_step,
        "findings": findings,
        "recommendations": recommendations,
    })


# ── ae.support.diagnose_retry_due_to_credentials ─────────────────────

async def diagnose_retry_due_to_credentials(request_id: str) -> str:
    """Diagnose a request in Retry state likely caused by credential pool exhaustion."""
    client = get_ae_client()

    req = client.get_request(request_id)
    status = req.get("status", "UNKNOWN")
    error = req.get("errorMessage") or req.get("errorDetails") or ""
    wf_name = req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name") or ""

    findings: list[str] = []
    pool_info = None

    if status != "Retry":
        findings.append(f"Request is in '{status}' state, not Retry.")

    err_lower = error.lower()
    if "credential" in err_lower or "pool" in err_lower:
        findings.append("Error message explicitly mentions credentials/pool.")
    elif "retry" in err_lower:
        findings.append("Error mentions retry but not specifically credentials.")
    else:
        findings.append("Error message does not clearly indicate credential issue.")

    if wf_name:
        try:
            wf = client.get_workflow(wf_name)
            pool_ref = wf.get("credentialPool") or wf.get("credentialPoolName") or wf.get("credentialPoolId")
            if pool_ref:
                findings.append(f"Workflow uses credential pool: {pool_ref}")
                try:
                    pool = client.get_credential_pool(str(pool_ref))
                    pool_info = {
                        "pool_id": pool_ref,
                        "pool_name": pool.get("poolName") or pool.get("name"),
                        "total": pool.get("totalCredentials") or pool.get("total"),
                        "available": pool.get("availableCredentials") or pool.get("available"),
                        "in_use": pool.get("inUseCredentials") or pool.get("inUse"),
                    }
                    avail = pool_info.get("available")
                    if avail is not None and int(avail) == 0:
                        findings.append("Pool has ZERO available credentials — this is the likely cause.")
                    elif avail is not None:
                        findings.append(f"Pool has {avail} available credentials — may have been a transient lock.")
                except Exception:
                    findings.append(f"Could not fetch pool '{pool_ref}' details.")
            else:
                findings.append("No credential pool reference found on the workflow.")
        except Exception:
            findings.append("Could not fetch workflow configuration.")

    retrying_count = 0
    try:
        retrying = client.search_requests(filters={"status": "Retry", "workflowName": wf_name}, limit=20)
        retrying_count = len(retrying)
        if retrying_count > 1:
            findings.append(f"{retrying_count} requests for this workflow are in Retry — possible pool saturation.")
    except Exception:
        pass

    return _safe_json({
        "request_id": request_id,
        "status": status,
        "workflow_name": wf_name,
        "error_message": error,
        "credential_pool": pool_info,
        "other_retrying_count": retrying_count,
        "findings": findings,
    })


# ── ae.support.diagnose_no_output_file ────────────────────────────────

async def diagnose_no_output_file(request_id: str) -> str:
    """Diagnose why a completed request produced no output file."""
    client = get_ae_client()

    req = client.get_request(request_id)
    status = req.get("status", "UNKNOWN")
    output = req.get("outputParams") or req.get("outputParameters") or req.get("resultAttributes") or {}
    wf_resp = req.get("workflowResponse")

    findings: list[str] = []

    if status not in ("Complete", "Completed", "COMPLETE"):
        findings.append(f"Request status is '{status}' — incomplete requests may not produce output.")

    file_refs = []
    if isinstance(output, dict):
        for k, v in output.items():
            if any(kw in k.lower() for kw in ("file", "path", "output", "attachment", "document")):
                file_refs.append({"key": k, "value": v})
    if isinstance(output, list):
        for p in output:
            if isinstance(p, dict):
                n = p.get("name", "")
                if any(kw in n.lower() for kw in ("file", "path", "output")):
                    file_refs.append({"key": n, "value": p.get("value")})

    if file_refs:
        findings.append(f"Found {len(file_refs)} file-related output fields.")
        for fr in file_refs:
            if not fr["value"] or fr["value"] in ("null", "None", ""):
                findings.append(f"Output '{fr['key']}' is empty/null — file was not produced.")
            else:
                findings.append(f"Output '{fr['key']}' has value: {fr['value']}")
    else:
        findings.append("No file-related output fields found in request output.")

    if wf_resp:
        try:
            parsed = json.loads(wf_resp) if isinstance(wf_resp, str) else wf_resp
            if isinstance(parsed, dict):
                msg = parsed.get("message") or parsed.get("error") or ""
                if msg:
                    findings.append(f"Workflow response: {msg}")
        except Exception:
            pass

    error_step = None
    try:
        steps = client.get_request_steps(request_id)
        if isinstance(steps, list):
            for s in steps:
                if isinstance(s, dict):
                    name = (s.get("stepName") or s.get("name") or "").lower()
                    if any(kw in name for kw in ("file", "output", "write", "export", "generate")):
                        st = (s.get("status") or "").upper()
                        if st in ("FAILURE", "ERROR", "FAILED", "SKIPPED"):
                            error_step = {"step": s.get("stepName") or s.get("name"), "status": st}
                            findings.append(f"File-related step '{error_step['step']}' has status: {st}")
    except Exception:
        pass

    if not findings:
        findings.append("No clear reason found for missing output. Check workflow design.")

    return _safe_json({
        "request_id": request_id,
        "status": status,
        "workflow_name": req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name"),
        "file_outputs": file_refs,
        "failing_file_step": error_step,
        "findings": findings,
    })


# ── ae.support.diagnose_schedule_not_triggered ────────────────────────

async def diagnose_schedule_not_triggered(schedule_id: str) -> str:
    """Diagnose why a scheduled job never started."""
    client = get_ae_client()

    data = client.get_schedule(schedule_id)
    enabled = data.get("enabled") or data.get("active")
    cron = data.get("cron") or data.get("cronExpression")
    wf_id = data.get("workflowId") or data.get("workflowName") or ""
    skip_ongoing = data.get("skipIfOngoing") or data.get("skipIfRunning")
    last_run = data.get("lastRun") or data.get("lastRunTime")

    findings: list[str] = []

    if not enabled:
        findings.append("Schedule is DISABLED.")
    if not cron:
        findings.append("No cron expression configured.")
    if skip_ongoing:
        findings.append("'Skip if ongoing' is enabled.")

    if wf_id:
        try:
            wf = client.get_workflow(str(wf_id))
            if not wf.get("active", True):
                findings.append(f"Workflow '{wf_id}' is INACTIVE.")
            agents = wf.get("assignedAgents") or []
            if not agents:
                findings.append("No agents assigned to the workflow.")
            else:
                try:
                    all_agents = client.list_agents()
                    for agent_ref in agents:
                        aname = agent_ref if isinstance(agent_ref, str) else (agent_ref.get("agentName") or agent_ref.get("name") or "")
                        match = next((a for a in all_agents if (a.get("agentName") or a.get("name")) == aname), None)
                        if match:
                            st = (match.get("agentState") or match.get("state") or "").upper()
                            if st not in ("CONNECTED", "RUNNING", "ACTIVE"):
                                findings.append(f"Assigned agent '{aname}' is {st} — not available.")
                except Exception:
                    pass
        except Exception as e:
            findings.append(f"Could not verify workflow: {e}")

    if skip_ongoing and wf_id:
        try:
            running = client.search_requests(
                filters={"workflowName": str(wf_id), "status": "Running"}, limit=5
            )
            if running:
                findings.append(f"{len(running)} running instance(s) found — skip-if-ongoing may have blocked the trigger.")
        except Exception:
            pass

    if not findings:
        findings.append("No obvious issues found. Check platform logs for scheduler errors.")

    return _safe_json({
        "schedule_id": schedule_id,
        "enabled": enabled,
        "cron": cron,
        "workflow": wf_id,
        "skip_if_ongoing": skip_ongoing,
        "last_run": last_run,
        "findings": findings,
    })


# ── ae.support.diagnose_user_cannot_find_workflow ─────────────────────

async def diagnose_user_cannot_find_workflow(
    user_id: str,
    workflow_id: str,
) -> str:
    """Diagnose why a user cannot see or run a specific workflow."""
    client = get_ae_client()
    findings: list[str] = []

    user_wfs = []
    try:
        user_wfs = client.get_user_workflows(user_id)
    except Exception as e:
        findings.append(f"Could not fetch user's workflow list: {e}")

    has_access = any(
        (w.get("workflowId") or w.get("id") or "") == workflow_id
        or (w.get("workflowName") or w.get("name") or "") == workflow_id
        for w in user_wfs
    )
    if has_access:
        findings.append("Workflow IS visible to the user — the issue may be UI/cache related.")
    else:
        findings.append("Workflow is NOT in user's accessible list.")

    try:
        wf = client.get_workflow(workflow_id)
        if not wf.get("active", True):
            findings.append("Workflow is INACTIVE — disabled workflows may be hidden.")
    except Exception:
        findings.append(f"Workflow '{workflow_id}' not found — it may not exist or name may be wrong.")

    try:
        perms = client.get_workflow_permissions(workflow_id)
        if isinstance(perms, dict):
            users = perms.get("users") or perms.get("allowedUsers") or []
            groups = perms.get("groups") or perms.get("allowedGroups") or []
            if users or groups:
                if user_id not in [str(u) for u in users]:
                    findings.append(f"User not in ACL. Allowed users: {users[:5]}. Groups: {groups[:5]}.")
                else:
                    findings.append("User IS in the workflow's permission list.")
    except Exception:
        pass

    if not findings:
        findings.append("No issues detected — user should have access.")

    return _safe_json({
        "user_id": user_id,
        "workflow_id": workflow_id,
        "user_has_access": has_access,
        "findings": findings,
    })


# ── ae.support.diagnose_awaiting_input ────────────────────────────────

async def diagnose_awaiting_input(request_id: str) -> str:
    """Diagnose why a request is in Awaiting Input state."""
    client = get_ae_client()

    req = client.get_request(request_id)
    status = req.get("status", "UNKNOWN")

    findings: list[str] = []
    task_info = req.get("taskInfo") or req.get("manualTask") or {}
    task_id = task_info.get("taskId") or task_info.get("id") or req.get("pendingTaskId")

    if status != "Awaiting Input":
        findings.append(f"Request is in '{status}' state, not 'Awaiting Input'.")

    blocking_tasks = []
    if task_id:
        try:
            task = client.get_task(task_id)
            blocking_tasks.append({
                "task_id": task_id,
                "assignee": task.get("assignee") or task.get("assignedTo"),
                "group": task.get("group") or task.get("assignedGroup"),
                "status": task.get("status"),
                "created": task.get("createdDate"),
                "deadline": task.get("deadline") or task.get("dueDate"),
            })
            assignee = task.get("assignee") or task.get("assignedTo") or "unassigned"
            findings.append(f"Blocking task {task_id} is assigned to: {assignee}")

            deadline = task.get("deadline") or task.get("dueDate")
            if deadline:
                findings.append(f"Task deadline: {deadline}")
        except Exception:
            findings.append(f"Could not fetch task {task_id} details.")
    else:
        findings.append("No pending task ID found on the request.")
        try:
            tasks = client.get_tasks({"requestId": request_id, "status": "Pending"})
            for t in tasks:
                blocking_tasks.append({
                    "task_id": t.get("id") or t.get("taskId"),
                    "assignee": t.get("assignee") or t.get("assignedTo"),
                    "group": t.get("group") or t.get("assignedGroup"),
                    "status": t.get("status"),
                })
            if blocking_tasks:
                findings.append(f"Found {len(blocking_tasks)} pending tasks via search.")
        except Exception:
            pass

    if not blocking_tasks:
        findings.append("No blocking tasks found — request may be awaiting external input.")

    return _safe_json({
        "request_id": request_id,
        "status": status,
        "workflow_name": req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name"),
        "blocking_tasks": blocking_tasks,
        "findings": findings,
    })


# ── ae.support.diagnose_agent_unavailable ─────────────────────────────

async def diagnose_agent_unavailable(agent_id: str) -> str:
    """Diagnose why an agent is unavailable."""
    client = get_ae_client()
    findings: list[str] = []

    agent_data = None
    try:
        agents = client.list_agents()
        agent_data = next(
            (a for a in agents
             if (a.get("agentId") or a.get("id") or "") == agent_id
             or (a.get("agentName") or a.get("name") or "") == agent_id),
            None,
        )
    except Exception as e:
        findings.append(f"Could not fetch agent list: {e}")

    if not agent_data:
        try:
            agent_data = client.get_agent(agent_id)
        except Exception:
            findings.append(f"Agent '{agent_id}' not found in monitoring or direct lookup.")
            return _safe_json({
                "agent_id": agent_id,
                "found": False,
                "findings": findings,
            })

    state = (agent_data.get("agentState") or agent_data.get("state") or "UNKNOWN").upper()
    agent_name = agent_data.get("agentName") or agent_data.get("name")
    findings.append(f"Agent '{agent_name}' is in state: {state}")

    if state in ("CONNECTED", "RUNNING", "ACTIVE"):
        findings.append("Agent appears healthy — issue may be transient or load-related.")
        try:
            running = client.get_agent_requests(agent_id)
            if len(running) > 0:
                findings.append(f"Agent has {len(running)} running requests — may be at capacity.")
        except Exception:
            pass
    elif state in ("STOPPED", "DISCONNECTED", "OFFLINE"):
        findings.append("Agent is offline — needs restart or connectivity fix.")
        last_seen = agent_data.get("lastSeen") or agent_data.get("lastHeartbeat")
        if last_seen:
            findings.append(f"Last seen: {last_seen}")
    elif state == "UNKNOWN":
        findings.append("Agent state is unknown — may be unreachable or recently deployed.")

    controller = agent_data.get("controllerName") or agent_data.get("controller")
    if controller:
        findings.append(f"Controller: {controller}")

    recommendations = []
    if state in ("STOPPED", "DISCONNECTED", "OFFLINE"):
        recommendations.append("Try ae.agent.restart_service to restart the agent.")
        recommendations.append("Verify network connectivity to the agent machine.")
    elif state == "UNKNOWN":
        recommendations.append("Check if the agent machine is running and accessible.")

    return _safe_json({
        "agent_id": agent_id,
        "agent_name": agent_name,
        "state": state,
        "found": True,
        "controller": controller,
        "last_seen": agent_data.get("lastSeen") or agent_data.get("lastHeartbeat"),
        "findings": findings,
        "recommendations": recommendations,
    })


# ── P1 support: diagnose_rdp_blocked_workflow ──────────────────────────

async def diagnose_rdp_blocked_workflow(request_id: str) -> str:
    """Diagnose RDP/UI blockage (workflow waiting on desktop or RDP session)."""
    client = get_ae_client()
    req = client.get_request(request_id)
    agent_name = req.get("agentName")
    findings: list[str] = []
    rdp_info = {}

    if agent_name:
        try:
            agents = client.list_agents()
            match = next((a for a in agents if (a.get("agentName") or a.get("name")) == agent_name), None)
            if match:
                rdp_info = {
                    "rdp_session_active": match.get("rdpSessionActive") or match.get("desktopSessionActive"),
                    "rdp_user": match.get("rdpUser") or match.get("desktopUser"),
                    "screen_locked": match.get("screenLocked") or match.get("isLocked"),
                }
                if rdp_info.get("screen_locked"):
                    findings.append("Agent desktop is reported as locked — workflow may be blocked on UI.")
                if rdp_info.get("rdp_session_active"):
                    findings.append("RDP/desktop session is active — check if a user session is blocking automation.")
        except Exception:
            pass

    status = req.get("status", "UNKNOWN")
    if status == "Running":
        findings.append("Request is still Running — may be stuck on a desktop/RDP step.")
    err = req.get("errorMessage") or ""
    if "rdp" in err.lower() or "desktop" in err.lower() or "session" in err.lower():
        findings.append("Error message mentions RDP/desktop/session.")

    if not findings:
        findings.append("No clear RDP/desktop blockage identified; check step logs for UI-related steps.")

    return _safe_json({
        "request_id": request_id,
        "workflow_name": req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name"),
        "agent_name": agent_name,
        "rdp_info": rdp_info,
        "findings": findings,
    })


# ── P1 support: build_case_snapshot ─────────────────────────────────────

async def build_case_snapshot(request_id: str, include_logs_summary: bool = True) -> str:
    """Create a support package for a case (request + summary + optional log count)."""
    from mcp_server.tools.request_read import request_build_support_snapshot
    snapshot_str = await request_build_support_snapshot(request_id)
    snapshot = json.loads(snapshot_str)
    if include_logs_summary:
        try:
            logs = get_ae_client().get_request_logs(request_id, tail=50)
            snapshot["logs_summary_count"] = len(logs) if isinstance(logs, list) else None
        except Exception:
            snapshot["logs_summary_count"] = None
    snapshot["case_snapshot"] = True
    return _safe_json(snapshot)


# ── P1 support: prepare_human_handoff_note ──────────────────────────────

async def prepare_human_handoff_note(request_id: str) -> str:
    """Generate a concise handoff note for human support."""
    from mcp_server.tools.request_diag import request_generate_support_narrative
    narrative_str = await request_generate_support_narrative(request_id)
    data = json.loads(narrative_str)
    narrative = data.get("narrative", "")
    client = get_ae_client()
    req = client.get_request(request_id)
    handoff = (
        f"## Handoff: Request {request_id}\n\n"
        f"{narrative}\n\n"
        f"**Next steps:** Review request details (ae.request.get_summary), step logs (ae.request.get_step_logs), "
        f"or run full diagnosis (ae.support.diagnose_failed_request) as needed."
    )
    return _safe_json({"request_id": request_id, "handoff_note": handoff})
