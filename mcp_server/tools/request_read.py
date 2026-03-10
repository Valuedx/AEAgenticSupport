"""
P0 request_read tools — 14 tools for reading/searching automation requests.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.request_read")


def _ts_to_iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
        except Exception:
            return str(ts)
    return str(ts)


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


# ── ae.request.get_by_id ─────────────────────────────────────────────

async def request_get_by_id(request_id: str) -> str:
    """Fetch full request record by ID."""
    data = get_ae_client().get_request(request_id)
    return _safe_json(data)


# ── ae.request.get_status ────────────────────────────────────────────

async def request_get_status(request_id: str) -> str:
    """Fetch current request status with timestamps."""
    data = get_ae_client().get_request(request_id)
    return _safe_json({
        "request_id": request_id,
        "status": data.get("status", "UNKNOWN"),
        "created": _ts_to_iso(data.get("createdDate")),
        "last_updated": _ts_to_iso(data.get("lastUpdatedDate")),
        "completed": _ts_to_iso(data.get("completedDate")),
        "picked_up": _ts_to_iso(data.get("pickedDate")),
    })


# ── ae.request.get_summary ───────────────────────────────────────────

async def request_get_summary(request_id: str) -> str:
    """One-shot request support summary for triage."""
    data = get_ae_client().get_request(request_id)
    wf_config = data.get("workflowConfiguration") or {}
    return _safe_json({
        "request_id": request_id,
        "workflow_name": data.get("workflowName") or wf_config.get("name"),
        "workflow_id": data.get("workflowId") or wf_config.get("id"),
        "status": data.get("status"),
        "submitted_by": data.get("userId") or data.get("submittedBy"),
        "agent_name": data.get("agentName"),
        "error_message": data.get("errorMessage") or data.get("errorDetails"),
        "created": _ts_to_iso(data.get("createdDate")),
        "completed": _ts_to_iso(data.get("completedDate")),
        "duration_ms": data.get("duration") or data.get("executionTime"),
        "source": data.get("source"),
        "priority": data.get("priority"),
    })


# ── ae.request.search ────────────────────────────────────────────────

async def request_search(
    workflow: str = "",
    user: str = "",
    status: str = "",
    agent: str = "",
    time_range_hours: int = 24,
    limit: int = 50,
) -> str:
    """Search requests by filters."""
    filters: dict[str, Any] = {}
    if workflow:
        filters["workflowName"] = workflow
    if user:
        filters["userId"] = user
    if status:
        filters["status"] = status
    if agent:
        filters["agentName"] = agent
    if time_range_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        filters["fromDate"] = int(cutoff.timestamp() * 1000)

    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "status": r.get("status"),
            "agent": r.get("agentName"),
            "user": r.get("userId"),
            "created": _ts_to_iso(r.get("createdDate")),
            "error": r.get("errorMessage"),
        })
    return _safe_json({"results": items, "count": len(items)})


# ── ae.request.list_for_user ─────────────────────────────────────────

async def request_list_for_user(user_id: str, limit: int = 50) -> str:
    """Get requests submitted by a user."""
    results = get_ae_client().search_requests(
        filters={"userId": user_id}, limit=limit
    )
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "status": r.get("status"),
            "created": _ts_to_iso(r.get("createdDate")),
            "error": r.get("errorMessage"),
        })
    return _safe_json({"user_id": user_id, "results": items, "count": len(items)})


# ── ae.request.list_for_workflow ──────────────────────────────────────

async def request_list_for_workflow(
    workflow_id: str,
    time_range_hours: int = 24,
    limit: int = 50,
) -> str:
    """Get requests for a specific workflow."""
    filters: dict[str, Any] = {"workflowName": workflow_id}
    if time_range_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        filters["fromDate"] = int(cutoff.timestamp() * 1000)

    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "status": r.get("status"),
            "agent": r.get("agentName"),
            "created": _ts_to_iso(r.get("createdDate")),
            "completed": _ts_to_iso(r.get("completedDate")),
            "error": r.get("errorMessage"),
        })
    return _safe_json({"workflow": workflow_id, "results": items, "count": len(items)})


# ── ae.request.list_by_status ────────────────────────────────────────

async def request_list_by_status(
    status: str,
    time_range_hours: int = 24,
    limit: int = 50,
) -> str:
    """Fetch requests in a specific status."""
    filters: dict[str, Any] = {"status": status}
    if time_range_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        filters["fromDate"] = int(cutoff.timestamp() * 1000)

    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "agent": r.get("agentName"),
            "created": _ts_to_iso(r.get("createdDate")),
            "error": r.get("errorMessage"),
        })
    return _safe_json({"status": status, "results": items, "count": len(items)})


# ── ae.request.list_stuck ────────────────────────────────────────────

async def request_list_stuck(
    threshold_minutes: int = 60,
    workflow: str = "",
    agent: str = "",
    limit: int = 50,
) -> str:
    """Detect stuck requests (running longer than threshold)."""
    filters: dict[str, Any] = {"status": "Running"}
    if workflow:
        filters["workflowName"] = workflow
    if agent:
        filters["agentName"] = agent
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    filters["toDate"] = int(cutoff.timestamp() * 1000)

    results = get_ae_client().search_requests(filters=filters, limit=limit * 2)
    stuck = []
    now = datetime.now(timezone.utc)
    for r in results:
        created_ts = r.get("createdDate") or r.get("pickedDate")
        if isinstance(created_ts, (int, float)):
            created = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc)
            age_min = (now - created).total_seconds() / 60
            if age_min >= threshold_minutes:
                stuck.append({
                    "request_id": r.get("id") or r.get("automationRequestId"),
                    "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
                    "agent": r.get("agentName"),
                    "running_minutes": round(age_min, 1),
                    "created": _ts_to_iso(created_ts),
                })
        if len(stuck) >= limit:
            break
    return _safe_json({
        "threshold_minutes": threshold_minutes,
        "stuck_requests": stuck,
        "count": len(stuck),
    })


# ── ae.request.list_failed_recently ──────────────────────────────────

async def request_list_failed_recently(
    time_range_hours: int = 24,
    workflow: str = "",
    limit: int = 50,
) -> str:
    """List recently failed requests."""
    filters: dict[str, Any] = {"status": "Failure"}
    if workflow:
        filters["workflowName"] = workflow
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(time_range_hours, 1))
    filters["fromDate"] = int(cutoff.timestamp() * 1000)

    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "agent": r.get("agentName"),
            "error_message": r.get("errorMessage") or r.get("errorDetails"),
            "created": _ts_to_iso(r.get("createdDate")),
            "completed": _ts_to_iso(r.get("completedDate")),
        })
    return _safe_json({
        "time_range_hours": time_range_hours,
        "failures": items,
        "count": len(items),
    })


# ── ae.request.list_retrying ─────────────────────────────────────────

async def request_list_retrying(
    workflow: str = "",
    agent: str = "",
    limit: int = 50,
) -> str:
    """List requests currently in Retry status."""
    filters: dict[str, Any] = {"status": "Retry"}
    if workflow:
        filters["workflowName"] = workflow
    if agent:
        filters["agentName"] = agent

    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "agent": r.get("agentName"),
            "created": _ts_to_iso(r.get("createdDate")),
            "error": r.get("errorMessage"),
        })
    return _safe_json({"results": items, "count": len(items)})


# ── ae.request.list_awaiting_input ────────────────────────────────────

async def request_list_awaiting_input(
    workflow: str = "",
    limit: int = 50,
) -> str:
    """List requests blocked waiting for human input."""
    filters: dict[str, Any] = {"status": "Awaiting Input"}
    if workflow:
        filters["workflowName"] = workflow

    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "agent": r.get("agentName"),
            "user": r.get("userId"),
            "created": _ts_to_iso(r.get("createdDate")),
        })
    return _safe_json({"results": items, "count": len(items)})


# ── ae.request.get_logs ───────────────────────────────────────────────

async def request_get_logs(request_id: str, tail: int = 100) -> str:
    """Retrieve raw execution logs for a request (tail)."""
    client = get_ae_client()
    data = client.get_request_logs(request_id, tail=tail)
    
    # Handle the fact that get_request_logs now returns a list of lines/dicts
    log_lines = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                log_lines.append(entry.get("message") or entry.get("details") or str(entry))
            else:
                log_lines.append(str(entry))
    
    return _safe_json({
        "request_id": request_id,
        "logs": log_lines,
        "count": len(log_lines),
    })


# ── ae.request.get_input_parameters ──────────────────────────────────

async def request_get_input_parameters(
    request_id: str,
    mask_sensitive: bool = True,
) -> str:
    """Get runtime input parameters of a request."""
    data = get_ae_client().get_request(request_id)
    params = data.get("params") or data.get("parameters") or data.get("inputParameters") or []
    if isinstance(params, list):
        param_map = {}
        for p in params:
            if isinstance(p, dict):
                name = p.get("name", "")
                val = p.get("value", "")
                if mask_sensitive and any(
                    kw in name.lower() for kw in ("password", "secret", "token", "key", "credential")
                ):
                    val = "***MASKED***"
                param_map[name] = val
        params = param_map
    return _safe_json({"request_id": request_id, "input_parameters": params})


# ── ae.request.get_failure_message ────────────────────────────────────

async def request_get_failure_message(request_id: str) -> str:
    """Fetch the latest failure/error message for a request."""
    data = get_ae_client().get_request(request_id)
    error = (
        data.get("errorMessage")
        or data.get("errorDetails")
        or data.get("failureMessage")
        or ""
    )
    wf_resp = data.get("workflowResponse")
    detail = ""
    if wf_resp:
        try:
            parsed = json.loads(wf_resp) if isinstance(wf_resp, str) else wf_resp
            detail = parsed.get("message") or parsed.get("error") or ""
        except Exception:
            detail = str(wf_resp)

    return _safe_json({
        "request_id": request_id,
        "status": data.get("status"),
        "error_message": error,
        "workflow_response_detail": detail,
    })


# ── ae.request.build_support_snapshot ─────────────────────────────────

async def request_build_support_snapshot(request_id: str) -> str:
    """Build a structured triage payload for a support case."""
    client = get_ae_client()
    data = client.get_request(request_id)
    wf_config = data.get("workflowConfiguration") or {}

    logs = None
    try:
        logs = client.get_request_logs(request_id, tail=50)
    except Exception:
        pass

    steps = None
    try:
        steps = client.get_request_steps(request_id)
    except Exception:
        pass

    error_step = None
    if isinstance(steps, list):
        for s in reversed(steps):
            if isinstance(s, dict) and s.get("status") in ("Failure", "Error", "Failed"):
                error_step = {
                    "step_name": s.get("stepName") or s.get("name"),
                    "status": s.get("status"),
                    "error": s.get("errorMessage") or s.get("error"),
                }
                break

    params = data.get("params") or data.get("parameters") or []
    if isinstance(params, list):
        params = {
            p.get("name", ""): p.get("value", "")
            for p in params if isinstance(p, dict)
        }

    snapshot = {
        "request_id": request_id,
        "workflow_name": data.get("workflowName") or wf_config.get("name"),
        "workflow_id": data.get("workflowId") or wf_config.get("id"),
        "status": data.get("status"),
        "submitted_by": data.get("userId") or data.get("submittedBy"),
        "agent_name": data.get("agentName"),
        "source": data.get("source"),
        "created": _ts_to_iso(data.get("createdDate")),
        "picked": _ts_to_iso(data.get("pickedDate")),
        "completed": _ts_to_iso(data.get("completedDate")),
        "duration_ms": data.get("duration") or data.get("executionTime"),
        "error_message": data.get("errorMessage") or data.get("errorDetails"),
        "input_parameters": params,
        "failing_step": error_step,
        "recent_log_count": len(logs) if isinstance(logs, list) else None,
    }
    return _safe_json(snapshot)


# ── P1 support: list_recent ───────────────────────────────────────────

async def request_list_recent(limit: int = 50, workflow: str = "", status: str = "") -> str:
    """List recent requests for support review."""
    filters: dict[str, Any] = {}
    if workflow:
        filters["workflowName"] = workflow
    if status:
        filters["status"] = status
    results = get_ae_client().search_requests(filters=filters, limit=limit)
    items = []
    for r in results[:limit]:
        items.append({
            "request_id": r.get("id") or r.get("automationRequestId"),
            "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
            "status": r.get("status"),
            "agent": r.get("agentName"),
            "user": r.get("userId"),
            "created": _ts_to_iso(r.get("createdDate")),
            "error": r.get("errorMessage"),
        })
    return _safe_json({"results": items, "count": len(items)})


# ── P1 support: get_source_context ─────────────────────────────────────

async def request_get_source_context(request_id: str) -> str:
    """Show trigger source: schedule, catalog, API, etc."""
    data = get_ae_client().get_request(request_id)
    source = data.get("source") or data.get("triggerSource") or "unknown"
    schedule_id = data.get("scheduleId") or data.get("schedule_id")
    catalog_ref = data.get("catalogRequestId") or data.get("catalog_id")
    return _safe_json({
        "request_id": request_id,
        "source_type": source,
        "schedule_id": schedule_id,
        "catalog_linkage": catalog_ref,
        "trigger_details": data.get("triggerDetails") or data.get("sourceContext"),
    })


# ── P1 support: get_time_details ────────────────────────────────────────

async def request_get_time_details(request_id: str) -> str:
    """Timing breakdown: created, picked, completed, duration."""
    data = get_ae_client().get_request(request_id)
    created = data.get("createdDate")
    picked = data.get("pickedDate")
    completed = data.get("completedDate")
    duration = data.get("duration") or data.get("executionTime")
    return _safe_json({
        "request_id": request_id,
        "created": _ts_to_iso(created),
        "picked_up": _ts_to_iso(picked),
        "completed": _ts_to_iso(completed),
        "duration_ms": duration,
        "queue_time_ms": (picked - created) if isinstance(picked, (int, float)) and isinstance(created, (int, float)) else None,
        "run_time_ms": (completed - picked) if isinstance(completed, (int, float)) and isinstance(picked, (int, float)) else None,
    })
