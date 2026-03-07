"""
P0 request_diag tools — 6 tools for request diagnostics.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.request_diag")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ── ae.request.get_execution_details ──────────────────────────────────

async def request_get_execution_details(request_id: str) -> str:
    """Fetch full execution metadata for a request."""
    data = get_ae_client().get_request(request_id)
    wf_config = data.get("workflowConfiguration") or {}

    return _safe_json({
        "request_id": request_id,
        "status": data.get("status"),
        "workflow_name": data.get("workflowName") or wf_config.get("name"),
        "workflow_id": data.get("workflowId") or wf_config.get("id"),
        "agent_name": data.get("agentName"),
        "agent_id": data.get("agentId"),
        "submitted_by": data.get("userId"),
        "source": data.get("source"),
        "priority": data.get("priority"),
        "created_date": data.get("createdDate"),
        "picked_date": data.get("pickedDate"),
        "completed_date": data.get("completedDate"),
        "duration": data.get("duration") or data.get("executionTime"),
        "error_message": data.get("errorMessage") or data.get("errorDetails"),
        "error_code": data.get("errorCode"),
        "retry_count": data.get("retryCount"),
        "parent_request_id": data.get("parentRequestId"),
        "root_request_id": data.get("rootRequestId"),
        "workflow_response": data.get("workflowResponse"),
        "input_parameters": data.get("params") or data.get("parameters"),
        "output_parameters": data.get("outputParams") or data.get("outputParameters"),
        "result_attributes": data.get("resultAttributes"),
    })


# ── ae.request.get_audit_logs ─────────────────────────────────────────

async def request_get_audit_logs(request_id: str) -> str:
    """Fetch the audit trail for a request."""
    client = get_ae_client()
    try:
        data = client.get_request_audit(request_id)
    except Exception:
        data = client.get_request_logs(request_id, tail=200)

    if isinstance(data, list):
        events = []
        for entry in data:
            if isinstance(entry, dict):
                events.append({
                    "timestamp": entry.get("timestamp") or entry.get("date"),
                    "action": entry.get("action") or entry.get("event") or entry.get("type"),
                    "user": entry.get("user") or entry.get("userId") or entry.get("performedBy"),
                    "details": entry.get("details") or entry.get("message"),
                })
        return _safe_json({"request_id": request_id, "audit_events": events, "count": len(events)})

    return _safe_json({"request_id": request_id, "audit_data": data})


# ── ae.request.get_step_logs ──────────────────────────────────────────

async def request_get_step_logs(request_id: str) -> str:
    """Fetch step-level execution logs for a request."""
    client = get_ae_client()
    try:
        data = client.get_request_steps(request_id)
    except Exception:
        data = client.get_request_logs(request_id, tail=200)

    if isinstance(data, list):
        steps = []
        for entry in data:
            if isinstance(entry, dict):
                steps.append({
                    "step_name": entry.get("stepName") or entry.get("name") or entry.get("nodeName"),
                    "status": entry.get("status") or entry.get("state"),
                    "start_time": entry.get("startTime") or entry.get("startDate"),
                    "end_time": entry.get("endTime") or entry.get("endDate"),
                    "duration": entry.get("duration") or entry.get("executionTime"),
                    "error": entry.get("errorMessage") or entry.get("error"),
                    "output": entry.get("output") or entry.get("result"),
                })
        return _safe_json({"request_id": request_id, "steps": steps, "count": len(steps)})

    return _safe_json({"request_id": request_id, "log_data": data})


# ── ae.request.get_live_progress ──────────────────────────────────────

async def request_get_live_progress(request_id: str) -> str:
    """Get current running state and active step for a live request."""
    data = get_ae_client().get_request(request_id)

    active_step = data.get("currentStep") or data.get("activeStep") or data.get("currentNodeName")
    progress = data.get("progress") or data.get("percentComplete")

    steps = None
    try:
        steps = get_ae_client().get_request_steps(request_id)
    except Exception:
        pass

    running_step = None
    completed_steps = 0
    if isinstance(steps, list):
        for s in steps:
            if isinstance(s, dict):
                st = s.get("status") or s.get("state") or ""
                if st.upper() in ("RUNNING", "EXECUTING", "IN_PROGRESS"):
                    running_step = s.get("stepName") or s.get("name")
                if st.upper() in ("COMPLETE", "COMPLETED", "SUCCESS"):
                    completed_steps += 1

    return _safe_json({
        "request_id": request_id,
        "status": data.get("status"),
        "active_step": active_step or running_step,
        "progress_percent": progress,
        "completed_steps": completed_steps,
        "total_steps": len(steps) if isinstance(steps, list) else None,
        "agent_name": data.get("agentName"),
    })


# ── ae.request.get_last_error_step ────────────────────────────────────

async def request_get_last_error_step(request_id: str) -> str:
    """Identify the step where failure occurred."""
    client = get_ae_client()
    data = client.get_request(request_id)

    try:
        steps = client.get_request_steps(request_id)
    except Exception:
        try:
            steps = client.get_request_logs(request_id, tail=200)
        except Exception:
            steps = []

    error_step = None
    last_success = None
    if isinstance(steps, list):
        for s in steps:
            if not isinstance(s, dict):
                continue
            st = (s.get("status") or s.get("state") or "").upper()
            name = s.get("stepName") or s.get("name") or s.get("nodeName")
            if st in ("FAILURE", "ERROR", "FAILED"):
                error_step = {
                    "step_name": name,
                    "status": s.get("status"),
                    "error_message": s.get("errorMessage") or s.get("error"),
                    "start_time": s.get("startTime") or s.get("startDate"),
                    "end_time": s.get("endTime") or s.get("endDate"),
                }
            elif st in ("COMPLETE", "COMPLETED", "SUCCESS"):
                last_success = name

    return _safe_json({
        "request_id": request_id,
        "overall_status": data.get("status"),
        "overall_error": data.get("errorMessage") or data.get("errorDetails"),
        "failing_step": error_step,
        "last_successful_step": last_success,
    })


# ── ae.request.get_manual_intervention_context ────────────────────────

async def request_get_manual_intervention_context(request_id: str) -> str:
    """Get details about HITL (human-in-the-loop) blocks on a request."""
    client = get_ae_client()
    data = client.get_request(request_id)

    task_info = data.get("taskInfo") or data.get("manualTask") or {}
    task_id = task_info.get("taskId") or task_info.get("id") or data.get("pendingTaskId")
    assignee = task_info.get("assignee") or task_info.get("assignedTo")
    pending_fields = task_info.get("pendingFields") or task_info.get("requiredFields") or []

    tasks = []
    if task_id:
        try:
            task_detail = client.get_task(task_id)
            tasks.append(task_detail)
        except Exception:
            pass

    if not tasks:
        try:
            all_tasks = client.get_tasks({"requestId": request_id, "status": "Pending"})
            tasks = all_tasks
        except Exception:
            pass

    task_summary = []
    for t in tasks:
        if isinstance(t, dict):
            task_summary.append({
                "task_id": t.get("id") or t.get("taskId"),
                "assignee": t.get("assignee") or t.get("assignedTo") or t.get("assignedUser"),
                "group": t.get("group") or t.get("assignedGroup"),
                "status": t.get("status"),
                "created": t.get("createdDate"),
                "deadline": t.get("deadline") or t.get("dueDate"),
            })

    return _safe_json({
        "request_id": request_id,
        "request_status": data.get("status"),
        "pending_task_id": task_id,
        "assignee": assignee,
        "pending_fields": pending_fields,
        "blocking_tasks": task_summary,
    })


# ── P1 support: get_last_successful_step ────────────────────────────────

async def request_get_last_successful_step(request_id: str) -> str:
    """Last successful step (for resume planning)."""
    client = get_ae_client()
    try:
        steps = client.get_request_steps(request_id)
    except Exception:
        steps = []
    last_success = None
    if isinstance(steps, list):
        for s in reversed(steps):
            if isinstance(s, dict):
                st = (s.get("status") or "").upper()
                if st in ("COMPLETE", "COMPLETED", "SUCCESS"):
                    last_success = {
                        "step_name": s.get("stepName") or s.get("name"),
                        "status": s.get("status"),
                        "end_time": s.get("endTime") or s.get("endDate"),
                    }
                    break
    return _safe_json({"request_id": request_id, "last_successful_step": last_success})


# ── P1 support: compare_attempts ────────────────────────────────────────

async def request_compare_attempts(request_id: str) -> str:
    """Compare multiple attempts (root + retries) for the same logical request."""
    client = get_ae_client()
    req = client.get_request(request_id)
    root_id = req.get("rootRequestId") or req.get("parentRequestId") or request_id
    chain = [request_id]
    if root_id != request_id:
        chain.insert(0, root_id)
    attempts = []
    for rid in chain[:10]:
        try:
            r = client.get_request(rid)
            attempts.append({
                "request_id": rid,
                "status": r.get("status"),
                "error": r.get("errorMessage") or r.get("errorDetails"),
                "created": r.get("createdDate"),
                "completed": r.get("completedDate"),
            })
        except Exception:
            pass
    return _safe_json({"root_request_id": root_id, "attempts": attempts})


# ── P1 support: export_diagnostic_bundle ────────────────────────────────

async def request_export_diagnostic_bundle(request_id: str) -> str:
    """Export case evidence: request, steps, logs summary for escalation."""
    client = get_ae_client()
    req = client.get_request(request_id)
    wf_config = req.get("workflowConfiguration") or {}
    try:
        steps = client.get_request_steps(request_id)
    except Exception:
        steps = []
    try:
        logs = client.get_request_logs(request_id, tail=100)
    except Exception:
        logs = []
    bundle = {
        "request_id": request_id,
        "workflow_name": req.get("workflowName") or wf_config.get("name"),
        "status": req.get("status"),
        "error_message": req.get("errorMessage") or req.get("errorDetails"),
        "submitted_by": req.get("userId"),
        "agent_name": req.get("agentName"),
        "created": req.get("createdDate"),
        "completed": req.get("completedDate"),
        "steps_count": len(steps) if isinstance(steps, list) else 0,
        "logs_count": len(logs) if isinstance(logs, list) else 0,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    return _safe_json(bundle)


# ── P1 support: generate_support_narrative ──────────────────────────────

async def request_generate_support_narrative(request_id: str) -> str:
    """Plain-language support summary for handoff."""
    client = get_ae_client()
    req = client.get_request(request_id)
    wf_name = req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name")
    status = req.get("status", "UNKNOWN")
    err = req.get("errorMessage") or req.get("errorDetails") or ""
    user = req.get("userId") or req.get("submittedBy") or "unknown"
    agent = req.get("agentName") or "unassigned"
    parts = [
        f"Request {request_id} (workflow: {wf_name}) is in status '{status}'.",
        f"Submitted by: {user}. Agent: {agent}.",
    ]
    if err:
        parts.append(f"Error: {err[:500]}")
    return _safe_json({"request_id": request_id, "narrative": " ".join(parts)})
