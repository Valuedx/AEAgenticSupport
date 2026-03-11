"""
P0 schedule_read (5 + 1 diagnose) + schedule_mutate (1) tools — 7 total.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.schedule")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


def _ts_to_iso(ts: Any) -> str | None:
    if ts is None:
        return None
    try:
        # Handle string-encoded timestamps or direct numbers
        val = float(ts)
        # T4 often returns high-precision milliseconds (13 digits)
        if val > 10000000000:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat().split('.')[0] + 'Z'
    except (ValueError, TypeError, OverflowError):
        return str(ts)


async def schedule_list_all(limit: int = 100) -> str:
    """List all schedules across all workflows. Use this to find a schedule_id before enabling or disabling."""
    results = get_ae_client().search_schedules(filters={"size": limit})
    items = []
    for s in results:
        sched_obj = s.get("schedule", {}) or {}
        exec_obj = sched_obj.get("execution", {}) or {}
        
        items.append({
            "schedule_id": s.get("id") or s.get("scheduleId"),
            "schedule_name": s.get("scheduleName") or s.get("name"),
            "workflow_id": s.get("workflowId"),
            "workflow_name": s.get("workflowName") or s.get("automationRequest", {}).get("workflowName"),
            "enabled": s.get("active") if s.get("active") is not None else s.get("enabled"),
            "cron": sched_obj.get("customCronExpression") or s.get("cron") or s.get("cronExpression"),
            "start_time": exec_obj.get("startTime"),
            "next_run": _ts_to_iso(s.get("nextRun") or s.get("nextRunTime") or s.get("startDatetime")),
            "last_run": _ts_to_iso(s.get("lastRun") or s.get("lastRunTime") or s.get("lastUpdatedDate")),
        })
    return _safe_json({
        "schedules": items, 
        "count": len(items),
        "hint": "To pause or disable a schedule, use 'ae.schedule.disable(schedule_id=\"ID\", reason=\"...\")'. To resume, use 'ae.schedule.enable(schedule_id=\"ID\", reason=\"...\")'."
    })


# ═══════════════════════════════════════════════════════════════════════
#  schedule_read
# ═══════════════════════════════════════════════════════════════════════

async def schedule_list_for_workflow(workflow_id: str) -> str:
    """Get schedules linked to a workflow. Use this to find a schedule_id if you know the workflow."""
    results = get_ae_client().search_schedules(workflow_id=workflow_id)
    items = []
    for s in results:
        sched_obj = s.get("schedule", {}) or {}
        exec_obj = sched_obj.get("execution", {}) or {}
        
        items.append({
            "schedule_id": s.get("id") or s.get("scheduleId"),
            "schedule_name": s.get("scheduleName") or s.get("name"),
            "workflow_id": s.get("workflowId"),
            "workflow_name": s.get("workflowName") or s.get("automationRequest", {}).get("workflowName"),
            "enabled": s.get("active") if s.get("active") is not None else s.get("enabled"),
            "cron": sched_obj.get("customCronExpression") or s.get("cron") or s.get("cronExpression"),
            "start_time": exec_obj.get("startTime"),
            "next_run": _ts_to_iso(s.get("nextRun") or s.get("nextRunTime") or s.get("startDatetime")),
            "last_run": _ts_to_iso(s.get("lastRun") or s.get("lastRunTime") or s.get("lastUpdatedDate")),
        })
    return _safe_json({"workflow_id": workflow_id, "schedules": items, "count": len(items)})


async def schedule_get_details(schedule_id: str) -> str:
    """Get full schedule configuration details."""
    data = get_ae_client().get_schedule(schedule_id)
    return _safe_json(data)


async def schedule_get_missed_runs(
    schedule_id: str,
    time_range_hours: int = 24,
) -> str:
    """Detect missed scheduled runs within a time range."""
    data = get_ae_client().get_schedule(schedule_id)

    missed = data.get("missedRuns") or data.get("missed") or []
    last_run = data.get("lastRun") or data.get("lastRunTime")
    next_run = data.get("nextRun") or data.get("nextRunTime")
    cron = data.get("cron") or data.get("cronExpression")
    enabled = data.get("enabled") or data.get("active")

    wf_id = data.get("workflowId") or data.get("workflowName") or ""
    recent_requests = []
    if wf_id:
        try:
            reqs = get_ae_client().search_requests(
                filters={
                    "workflowName": str(wf_id),
                    "source": "schedule",
                    "fromDate": int(
                        (datetime.now(timezone.utc) - timedelta(hours=time_range_hours)).timestamp() * 1000
                    ),
                },
                limit=20,
            )
            for r in reqs:
                recent_requests.append({
                    "request_id": r.get("id") or r.get("automationRequestId"),
                    "status": r.get("status"),
                    "created": _ts_to_iso(r.get("createdDate")),
                })
        except Exception:
            pass

    return _safe_json({
        "schedule_id": schedule_id,
        "enabled": enabled,
        "cron": cron,
        "last_run": _ts_to_iso(last_run),
        "next_run": _ts_to_iso(next_run),
        "missed_runs": missed,
        "missed_count": len(missed) if isinstance(missed, list) else 0,
        "recent_triggered_requests": recent_requests,
    })


async def schedule_get_recent_generated_requests(
    schedule_id: str,
    time_range_hours: int = 24,
) -> str:
    """Map schedule to the requests it generated."""
    data = get_ae_client().get_schedule(schedule_id)
    wf_id = data.get("workflowId") or data.get("workflowName") or ""

    requests = []
    if wf_id:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        try:
            reqs = get_ae_client().search_requests(
                filters={
                    "workflowName": str(wf_id),
                    "fromDate": int(cutoff.timestamp() * 1000),
                },
                limit=50,
            )
            for r in reqs:
                src = r.get("source") or ""
                requests.append({
                    "request_id": r.get("id") or r.get("automationRequestId"),
                    "status": r.get("status"),
                    "source": src,
                    "created": _ts_to_iso(r.get("createdDate")),
                    "completed": _ts_to_iso(r.get("completedDate")),
                    "likely_schedule_triggered": "schedule" in src.lower() or "cron" in src.lower(),
                })
        except Exception:
            pass

    return _safe_json({
        "schedule_id": schedule_id,
        "workflow": wf_id,
        "time_range_hours": time_range_hours,
        "requests": requests,
        "count": len(requests),
    })


async def schedule_diagnose_not_triggered(schedule_id: str) -> str:
    """One-shot diagnosis of why a schedule didn't trigger."""
    client = get_ae_client()
    data = client.get_schedule(schedule_id)

    enabled = data.get("enabled") or data.get("active")
    cron = data.get("cron") or data.get("cronExpression")
    last_run = data.get("lastRun") or data.get("lastRunTime")
    next_run = data.get("nextRun") or data.get("nextRunTime")
    wf_id = data.get("workflowId") or data.get("workflowName") or ""
    skip_ongoing = data.get("skipIfOngoing") or data.get("skipIfRunning")

    findings: list[str] = []
    if not enabled:
        findings.append("Schedule is DISABLED — it will not trigger.")
    if not cron:
        findings.append("No cron expression configured — schedule has no trigger timing.")

    wf_active = True
    if wf_id:
        try:
            wf = client.get_workflow(str(wf_id))
            if not wf.get("active", True):
                wf_active = False
                findings.append(f"Linked workflow '{wf_id}' is INACTIVE.")
        except Exception:
            findings.append(f"Could not verify linked workflow '{wf_id}' status.")

    if skip_ongoing:
        findings.append("'Skip if ongoing' is enabled — a running instance may have prevented the trigger.")

    agents_ok = True
    if wf_id:
        try:
            wf_data = client.get_workflow(str(wf_id))
            assigned = wf_data.get("assignedAgents") or []
            if not assigned:
                findings.append("No agents assigned to the workflow.")
                agents_ok = False
        except Exception:
            pass

    if not findings:
        findings.append("No obvious issues found. Schedule appears correctly configured.")

    return _safe_json({
        "schedule_id": schedule_id,
        "enabled": enabled,
        "cron": cron,
        "last_run": _ts_to_iso(last_run),
        "next_run": _ts_to_iso(next_run),
        "skip_if_ongoing": skip_ongoing,
        "workflow_active": wf_active,
        "findings": findings,
    })


# ═══════════════════════════════════════════════════════════════════════
#  schedule_mutate
# ═══════════════════════════════════════════════════════════════════════

async def schedule_disable(
    schedule_id: str,
    reason: str = "",
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Pause or disable a schedule. Guaded operation. 
    Use this when asked "How can I pause or disable this schedule?".
    Requires a schedule_id. If not known, list schedules first."""
    if dry_run:
        return _safe_json({
            "dry_run": True,
            "action": "disable_schedule",
            "schedule_id": schedule_id,
            "reason": reason,
            "message": f"Would disable schedule {schedule_id}. No changes made.",
        })
    data = get_ae_client().disable_schedule(schedule_id, reason=reason)
    return _safe_json({
        "success": True,
        "action": "disable_schedule",
        "schedule_id": schedule_id,
        "reason": reason,
        "requested_by": requested_by,
        "case_id": case_id,
        "raw": data,
    })


# ═══════════════════════════════════════════════════════════════════════
#  P1 support: get_next_runs, get_last_runs, enable, run_now
# ═══════════════════════════════════════════════════════════════════════

async def schedule_get_next_runs(schedule_id: str) -> str:
    """Upcoming run times for a schedule."""
    data = get_ae_client().get_schedule(schedule_id)
    next_runs = data.get("nextRun") or data.get("nextRuns") or data.get("upcomingRuns") or []
    if not isinstance(next_runs, list):
        next_runs = [next_runs] if next_runs else []
    return _safe_json({"schedule_id": schedule_id, "next_runs": next_runs})


async def schedule_get_last_runs(schedule_id: str, limit: int = 10) -> str:
    """Past runs for a schedule."""
    data = get_ae_client().get_schedule(schedule_id)
    last_runs = data.get("lastRun") or data.get("lastRuns") or data.get("runHistory") or []
    if not isinstance(last_runs, list):
        last_runs = [last_runs] if last_runs else []
    return _safe_json({"schedule_id": schedule_id, "last_runs": last_runs[:limit]})


async def schedule_enable(
    schedule_id: str,
    reason: str = "",
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Resume or enable a schedule. Guarded operation.
    Use this when asked "How do I resume or enable this schedule?".
    Requires a schedule_id. If not known, list schedules first."""
    if dry_run:
        return _safe_json({"dry_run": True, "action": "enable_schedule", "schedule_id": schedule_id, "reason": reason, "message": f"Would enable schedule {schedule_id}. No changes made."})
    data = get_ae_client().enable_schedule(schedule_id, reason=reason)
    return _safe_json({"success": True, "action": "enable_schedule", "schedule_id": schedule_id, "reason": reason, "requested_by": requested_by, "case_id": case_id, "raw": data})


async def schedule_run_now(
    schedule_id: str,
    reason: str,
    requested_by: str = "",
    case_id: str = "",
    dry_run: bool = False,
) -> str:
    """Trigger immediate run of a schedule. Guarded operation."""
    if dry_run:
        return _safe_json({"dry_run": True, "action": "run_schedule_now", "schedule_id": schedule_id, "reason": reason, "message": f"Would trigger schedule {schedule_id} now. No changes made."})
    data = get_ae_client().run_schedule_now(schedule_id, reason=reason)
    request_id = data.get("requestId") or data.get("id")
    return _safe_json({"success": True, "action": "run_schedule_now", "schedule_id": schedule_id, "triggered_request_id": request_id, "reason": reason, "requested_by": requested_by, "case_id": case_id, "raw": data})
