"""
Shared AutomationEdge MCP tool specifications.

This module is the single source of truth for tool metadata so the standalone
MCP server and the in-process app registry stay aligned as the MCP SDK evolves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property, lru_cache, wraps
import inspect
import json
from copy import deepcopy
from typing import Any, Callable

from mcp.server.fastmcp.tools.base import Tool as FastMCPTool
from mcp.types import ToolAnnotations

APP_CATEGORY_MAP = {
    "request_read": "status",
    "request_diag": "logs",
    "request_mutate": "remediation",
    "workflow_read": "dependency",
    "workflow_mutate": "remediation",
    "agent_read": "status",
    "agent_mutate": "remediation",
    "schedule_read": "dependency",
    "schedule_mutate": "remediation",
    "task_read": "status",
    "credential_read": "status",
    "user_read": "dependency",
    "permission_read": "dependency",
    "platform_read": "status",
    "result_read": "status",
    "support_composite": "status",
    "dependency": "dependency",
}

SAFETY_TO_TIER = {
    "safe_read": "read_only",
    "safe_mutation": "low_risk",
    "guarded": "medium_risk",
    "privileged": "high_risk",
}

_ALWAYS_AVAILABLE_MCP_TOOLS = {
    "ae.support.diagnose_failed_request",
    "ae.support.diagnose_stuck_running_request",
    "ae.support.diagnose_awaiting_input",
    "ae.support.diagnose_agent_unavailable",
    "ae.request.get_summary",
    "ae.request.get_failure_message",
    "ae.request.get_live_progress",
    "ae.agent.get_status",
    "ae.agent.get_details",
    "ae.agent.get_current_load",
    "ae.schedule.list_all",
    "ae.schedule.disable",
    "ae.schedule.enable",
}

_TAG_STOPWORDS = {"ae", "get", "list", "by", "for", "to", "and", "or"}

_COMMON_PARAMETER_DESCRIPTIONS = {
    "request_id": "AutomationEdge request ID to inspect or act on.",
    "workflow_id": "Workflow ID or workflow name recognized by AutomationEdge.",
    "agent_id": "Agent ID or agent name recognized by AutomationEdge.",
    "schedule_id": "Schedule ID to inspect or control.",
    "task_id": "Manual task ID to inspect or update.",
    "pool_id": "Credential pool identifier.",
    "user_id": "AutomationEdge user identifier.",
    "case_id": "External incident, ticket, or support case reference.",
    "requested_by": "Person or system requesting the action for audit context.",
    "reason": "Operational justification for the action. Required for guarded and privileged changes.",
    "dry_run": "If true, preview the action without changing AutomationEdge state.",
    "comment": "Human-readable support note to persist with the request or case.",
    "status": "AutomationEdge request status filter such as Failure, Retry, Running, or Awaiting Input.",
    "workflow": "Optional workflow name filter.",
    "agent": "Optional agent name filter.",
    "limit": "Maximum number of records to return.",
    "time_range_hours": "How far back to search, in hours.",
}

_CURATED_TOOL_OVERRIDES: dict[str, dict[str, Any]] = {
    "ae.request.restart_failed": {
        "title": "Request: Restart Failed Run",
        "description": "Restart a failed AutomationEdge request with the same inputs and routing context.",
        "use_when": "The request is in Failure and the underlying issue has already been corrected or was transient.",
        "avoid_when": "The failure was caused by bad inputs, missing dependencies, or permissions that are still unresolved.",
        "input_examples": [
            {
                "request_id": "REQ-10421",
                "reason": "Transient DB outage cleared; retry approved by support",
                "requested_by": "ops.l2",
                "case_id": "INC-4201",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "reason": "Why a restart is safe now, including what changed since the failure.",
        },
        "extra_tags": ["restart", "recovery", "failed-request"],
    },
    "ae.request.restart": {
        "title": "Request: Restart Workflow",
        "description": "Trigger a restart of an AutomationEdge request using the dedicated restart API (PUT).",
        "use_when": "You need to restart a request as per specific user instruction or recovery SOP.",
        "input_examples": [
            {
                "request_id": "2501865",
                "reason": "User requested restart via support console",
                "requested_by": "ops.l2",
                "case_id": "INC-4250",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "reason": "Reason for the restart (passed to the AE API).",
        },
        "extra_tags": ["restart", "workflow-restart", "remediation"],
    },
    "ae.request.terminate_running": {
        "title": "Request: Terminate Running Execution",
        "description": "Terminate a currently running AutomationEdge request that is hung, unsafe, or no longer needed.",
        "use_when": "The request is stuck, causing collateral impact, or continuing it would be riskier than stopping it.",
        "avoid_when": "The request is still making progress or a safer read-only diagnosis can confirm the next step first.",
        "input_examples": [
            {
                "request_id": "REQ-10455",
                "reason": "Execution hung for 90 minutes on locked desktop; stop before retry",
                "requested_by": "ops.l2",
                "case_id": "INC-4210",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "reason": "Why the running execution must be terminated and what user impact is being prevented.",
        },
        "extra_tags": ["terminate", "hung-request", "emergency-stop"],
    },
    "ae.request.resubmit_from_failure_point": {
        "title": "Request: Resume From Failure Point",
        "description": "Resume a failed request from the last known failure point instead of rerunning the full workflow.",
        "use_when": "A workflow supports resume semantics and rerunning from the failed step is lower risk than restarting from the beginning.",
        "avoid_when": "Earlier workflow state is suspect, upstream inputs changed, or resume semantics are unknown.",
        "input_examples": [
            {
                "request_id": "REQ-10499",
                "reason": "Network dependency recovered; resume from failed export step",
                "requested_by": "ops.l2",
                "case_id": "INC-4227",
                "dry_run": True,
            }
        ],
        "extra_tags": ["resume", "checkpoint", "failure-point"],
    },
    "ae.request.add_support_comment": {
        "title": "Request: Add Support Comment",
        "description": "Attach a support comment or action note to a request without changing execution state.",
        "use_when": "You need to record analysis, decisions, or next steps directly on the request timeline.",
        "avoid_when": "You need to change request state rather than just documenting context.",
        "input_examples": [
            {
                "request_id": "REQ-10421",
                "comment": "Reviewed logs; waiting on DBA confirmation before retry",
                "requested_by": "ops.l2",
                "case_id": "INC-4201",
                "dry_run": True,
            }
        ],
        "extra_tags": ["comment", "audit", "case-notes"],
    },
    "ae.request.cancel_new_or_retry": {
        "title": "Request: Cancel Queued Or Retrying Request",
        "description": "Cancel a request that has not started yet and is still in New or Retry state.",
        "use_when": "The queued execution should not proceed because the request is stale, duplicated, or blocked by a known issue.",
        "avoid_when": "The request is already running or the issue can be resolved without discarding the queued attempt.",
        "input_examples": [
            {
                "request_id": "REQ-10507",
                "reason": "Duplicate catalog submission; preserving newer request only",
                "requested_by": "ops.l2",
                "case_id": "INC-4233",
                "dry_run": True,
            }
        ],
        "extra_tags": ["cancel", "queue-control", "duplicate-request"],
    },
    "ae.request.resubmit_from_start": {
        "title": "Request: Resubmit From Start",
        "description": "Create a fresh rerun of the request from the beginning using the original inputs.",
        "use_when": "A full clean rerun is safer than resuming from a partial state.",
        "avoid_when": "The original inputs are wrong or the workflow supports a lower-risk resume option.",
        "input_examples": [
            {
                "request_id": "REQ-10511",
                "reason": "Partial execution state is unreliable; rerun full workflow after cleanup",
                "requested_by": "ops.l2",
                "case_id": "INC-4238",
                "dry_run": True,
            }
        ],
        "extra_tags": ["resubmit", "fresh-run", "full-rerun"],
    },
    "ae.request.tag_case_reference": {
        "title": "Request: Link Support Case",
        "description": "Attach an incident or case reference to a request for support traceability.",
        "use_when": "The request needs to be linked to an external support process or ticket.",
        "avoid_when": "No durable case or incident identifier exists yet.",
        "input_examples": [
            {
                "request_id": "REQ-10511",
                "case_id": "INC-4238",
                "requested_by": "ops.l2",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "case_id": "Incident, ticket, or case identifier to attach to the request trail.",
        },
        "extra_tags": ["link-case", "ticketing", "traceability"],
    },
    "ae.request.raise_manual_handoff": {
        "title": "Request: Raise Manual Handoff",
        "description": "Mark a request for human handling and leave a handoff note for the next team.",
        "use_when": "Automation should stop and ownership needs to move to human support or operations.",
        "avoid_when": "The issue can still be resolved safely through automated remediation.",
        "input_examples": [
            {
                "request_id": "REQ-10522",
                "comment": "Waiting for vendor confirmation on API contract mismatch",
                "requested_by": "ops.l2",
                "case_id": "INC-4241",
                "dry_run": True,
            }
        ],
        "extra_tags": ["handoff", "manual", "human-in-the-loop"],
    },
    "ae.workflow.enable": {
        "title": "Workflow: Re-enable Disabled Workflow",
        "description": "Re-enable a workflow that was previously disabled and allow new executions again.",
        "use_when": "The disable reason is resolved and the workflow can safely accept new runs.",
        "avoid_when": "Root cause validation is incomplete or downstream systems are still unstable.",
        "input_examples": [
            {
                "workflow_id": "Claims_Processing_Daily",
                "reason": "Input feed restored and smoke test passed",
                "requested_by": "ops.l2",
                "case_id": "INC-4300",
                "dry_run": True,
            }
        ],
        "extra_tags": ["workflow-enable", "resume-service"],
    },
    "ae.workflow.disable": {
        "title": "Workflow: Disable Workflow",
        "description": "Disable a workflow to prevent new executions while an incident or risky condition is active.",
        "use_when": "New runs would amplify user impact or repeatedly fail until a fix is deployed.",
        "avoid_when": "The issue affects only a single request and does not justify stopping the whole workflow.",
        "input_examples": [
            {
                "workflow_id": "Claims_Processing_Daily",
                "reason": "Corrupt upstream file feed causing repeated failures",
                "requested_by": "ops.l2",
                "case_id": "INC-4296",
                "dry_run": True,
            }
        ],
        "extra_tags": ["workflow-disable", "containment"],
    },
    "ae.workflow.assign_to_agent": {
        "title": "Workflow: Reassign Workflow To Agent",
        "description": "Assign a workflow to a specific agent when routing or capacity needs to change.",
        "use_when": "The current agent is unhealthy, overloaded, or missing required access.",
        "avoid_when": "The target agent has not been validated for connectivity, permissions, or desktop prerequisites.",
        "input_examples": [
            {
                "workflow_id": "Claims_Processing_Daily",
                "agent_id": "AE-AGENT-07",
                "reason": "Primary bot runner is offline; fail over to validated standby",
                "requested_by": "ops.l2",
                "case_id": "INC-4304",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "agent_id": "Target agent that should run future executions for this workflow.",
        },
        "extra_tags": ["reroute", "agent-assignment", "failover"],
    },
    "ae.workflow.update_permissions": {
        "title": "Workflow: Update Permissions",
        "description": "Change workflow access control permissions for users or groups.",
        "use_when": "A workflow ACL must be corrected to restore or restrict access for the right audience.",
        "avoid_when": "The access issue is caused by a missing role sync, group membership delay, or mistaken workflow identifier.",
        "input_examples": [
            {
                "workflow_id": "Claims_Processing_Daily",
                "permissions": {"users": ["ops.supervisor"], "groups": ["claims-ops"]},
                "reason": "Restore access after role cleanup",
                "requested_by": "iam.admin",
                "case_id": "INC-4310",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "permissions": "Permission payload to apply, typically containing allowed users and groups.",
        },
        "extra_tags": ["acl", "permissions", "access-control"],
    },
    "ae.workflow.rollback_version": {
        "title": "Workflow: Roll Back Workflow Version",
        "description": "Revert a workflow to a previous version when a recent change introduced failures or risk.",
        "use_when": "A known-good prior version exists and rollback is the fastest safe containment step.",
        "avoid_when": "The problem is environmental rather than version-specific, or rollback would break dependent changes.",
        "input_examples": [
            {
                "workflow_id": "Claims_Processing_Daily",
                "version": "v2026.03.05.1",
                "reason": "Latest release introduced validation regression",
                "requested_by": "release.manager",
                "case_id": "INC-4318",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "version": "Known-good workflow version identifier to roll back to.",
        },
        "extra_tags": ["rollback", "release-recovery", "version-control"],
    },
    "ae.agent.restart_service": {
        "title": "Agent: Restart Agent Service",
        "description": "Restart the AutomationEdge agent service on a target agent to recover from offline or unhealthy state.",
        "use_when": "The agent is stopped, disconnected, or clearly unhealthy and service restart is the standard recovery step.",
        "avoid_when": "The agent host has a broader OS or network issue that a service restart will not fix.",
        "input_examples": [
            {
                "agent_id": "AE-AGENT-07",
                "reason": "Agent heartbeat stale for 20 minutes; approved for service restart",
                "requested_by": "ops.l2",
                "case_id": "INC-4324",
                "dry_run": True,
            }
        ],
        "extra_tags": ["agent-restart", "heartbeat", "service-recovery"],
    },
    "ae.agent.clear_stale_rdp_session": {
        "title": "Agent: Clear Stale RDP Session",
        "description": "Clear a stale or blocking RDP/desktop session on an agent used by UI automation.",
        "use_when": "A workflow is blocked by a locked or stale desktop session on the agent.",
        "avoid_when": "The workflow is not desktop-dependent or the active session is still in legitimate use.",
        "input_examples": [
            {
                "agent_id": "AE-AGENT-12",
                "reason": "Desktop workflow blocked by stale RDP session after disconnect",
                "requested_by": "ops.l2",
                "case_id": "INC-4331",
                "dry_run": True,
            }
        ],
        "extra_tags": ["rdp", "desktop-automation", "session-recovery"],
    },
    "ae.schedule.run_now": {
        "title": "Schedule: Trigger Run Now",
        "description": "Trigger a schedule immediately outside its normal next-run time.",
        "use_when": "A missed or delayed scheduled run must be executed immediately after validation.",
        "avoid_when": "The next normal trigger is imminent or duplicate execution would cause business impact.",
        "input_examples": [
            {
                "schedule_id": "SCH-2001",
                "reason": "Missed 08:00 trigger due to controller failover; run once now",
                "requested_by": "ops.l2",
                "case_id": "INC-4342",
                "dry_run": True,
            }
        ],
        "extra_tags": ["run-now", "catch-up", "schedule-trigger"],
    },
    "ae.schedule.disable": {
        "title": "Schedule: Disable/Pause Schedule",
        "description": "Disable, pause, stop, or halt an AutomationEdge schedule. Use this when the user says 'pause the schedule', 'stop the trigger', 'disable runs', or 'halt automation' for a specific schedule_id.",
        "use_when": "The user wants to temporarily or permanently stop automated runs (e.g., 'pause schedule 1024' or 'disable the License_bot trigger').",
        "avoid_when": "Only a single queued request is problematic and the broader schedule is still healthy.",
        "input_examples": [
            {
                "schedule_id": "1024",
                "reason": "Prevent repeated batch failures until upstream feed is fixed",
                "requested_by": "ops.l2",
                "case_id": "INC-4345",
                "dry_run": True,
            }
        ],
        "extra_tags": ["schedule-disable", "pause-schedule", "stop-trigger", "stop-schedule", "halt-runs", "deactivate-trigger"],
    },
    "ae.schedule.enable": {
        "title": "Schedule: Enable/Resume Schedule",
        "description": "Enable, resume, start, or restart an AutomationEdge schedule. Use this when the user says 'resume the schedule', 'start the trigger', 're-enable runs', or 'unpause automation' for a specific schedule_id.",
        "use_when": "The user wants to allow a previously disabled or paused schedule to start triggering runs again (e.g., 'resume schedule 1024' or 'start the License_bot trigger').",
        "avoid_when": "The workflow or its dependencies are still unstable and automated triggering should remain suspended.",
        "input_examples": [
            {
                "schedule_id": "1024",
                "reason": "Downstream file drop restored; resume hourly schedule",
                "requested_by": "ops.l2",
                "case_id": "INC-4340",
                "dry_run": True,
            }
        ],
        "extra_tags": ["schedule-enable", "resume-schedule", "start-trigger", "start-schedule", "resume-runs", "reactivate-trigger"],
    },
    "ae.schedule.list_all": {
        "title": "Schedule: List All Schedules",
        "description": "List all schedules across all workflows. Use this to find the correct schedule_id.",
        "use_when": "The user wants to see schedules or before enabling/disabling if the ID is unknown.",
        "extra_tags": ["list-schedules", "find-schedule", "global-schedules"],
    },
    "ae.task.cancel_admin": {
        "title": "Task: Cancel Task As Admin",
        "description": "Cancel a pending manual task through admin controls.",
        "use_when": "The task is obsolete, duplicated, or blocking a request that should no longer wait for human input.",
        "avoid_when": "A reassignment or clarification would resolve the blockage without discarding the task.",
        "input_examples": [
            {
                "task_id": "TASK-7781",
                "reason": "Superseded by replacement request and no longer needed",
                "requested_by": "ops.l2",
                "case_id": "INC-4352",
                "dry_run": True,
            }
        ],
        "extra_tags": ["task-cancel", "awaiting-input"],
    },
    "ae.task.reassign": {
        "title": "Task: Reassign Task",
        "description": "Reassign a pending task to another user or group that can complete the required input.",
        "use_when": "The current assignee is unavailable or the task belongs with a different team.",
        "avoid_when": "The correct assignee already has the task and only needs more context or time.",
        "input_examples": [
            {
                "task_id": "TASK-7781",
                "target_user_or_group": "claims-ops",
                "reason": "Original assignee unavailable; route to owning group",
                "requested_by": "ops.l2",
                "case_id": "INC-4354",
                "dry_run": True,
            }
        ],
        "parameter_docs": {
            "target_user_or_group": "User or group that should receive the reassigned task.",
        },
        "extra_tags": ["task-reassign", "manual-intervention"],
    },
    "ae.support.diagnose_failed_request": {
        "title": "Support: Diagnose Failed Request",
        "description": "Produce a first-pass RCA for a failed request, including failure category, failing step, and recommended next checks.",
        "use_when": "You have a failed request ID and need the fastest safe diagnostic summary before taking action.",
        "avoid_when": "The request is still running or you already know the precise failure mechanism and need a specific follow-up tool.",
        "input_examples": [{"request_id": "REQ-10421"}],
        "extra_tags": ["rca", "failed-request", "triage"],
    },
    "ae.support.diagnose_stuck_running_request": {
        "title": "Support: Diagnose Stuck Running Request",
        "description": "Investigate a request that appears hung in Running state and summarize likely blockage points.",
        "use_when": "A request has been Running unusually long and you need agent, step, and hang indicators quickly.",
        "avoid_when": "The request just started and has not exceeded a meaningful runtime threshold.",
        "input_examples": [{"request_id": "REQ-10455"}],
        "extra_tags": ["hung-request", "running", "triage"],
    },
    "ae.support.diagnose_retry_due_to_credentials": {
        "title": "Support: Diagnose Credential Retry",
        "description": "Check whether a Retry state is caused by credential pool exhaustion or related credential issues.",
        "use_when": "The request is in Retry and the error suggests credential pool contention, lock, or availability problems.",
        "avoid_when": "Retry state is clearly caused by a non-credential error such as filesystem or business validation.",
        "input_examples": [{"request_id": "REQ-10499"}],
        "extra_tags": ["credentials", "retry", "pool-exhaustion"],
    },
    "ae.support.diagnose_no_output_file": {
        "title": "Support: Diagnose Missing Output",
        "description": "Determine why a completed request did not produce the expected output file or artifact.",
        "use_when": "The workflow completed but the caller expected a file, export, or generated artifact that is missing.",
        "avoid_when": "The workflow never reached completion or does not actually produce file-based output.",
        "input_examples": [{"request_id": "REQ-10540"}],
        "extra_tags": ["missing-output", "artifact", "file-diagnosis"],
    },
    "ae.support.diagnose_schedule_not_triggered": {
        "title": "Support: Diagnose Schedule Trigger Failure",
        "description": "Explain why a schedule did not trigger and summarize enablement, workflow, agent, and skip-if-running factors.",
        "use_when": "A scheduled workflow did not start when expected and you need the likely reasons in one response.",
        "avoid_when": "You already know the schedule is disabled and only need to confirm configuration details.",
        "input_examples": [{"schedule_id": "SCH-2001"}],
        "extra_tags": ["schedule", "trigger", "missed-run"],
    },
    "ae.support.diagnose_user_cannot_find_workflow": {
        "title": "Support: Diagnose Workflow Access Issue",
        "description": "Analyze why a user cannot see or run a workflow, including ACL and visibility checks.",
        "use_when": "A user reports a workflow missing from their catalog or they cannot launch it.",
        "avoid_when": "The issue is with a running request rather than workflow visibility or access.",
        "input_examples": [{"user_id": "jdoe", "workflow_id": "Claims_Processing_Daily"}],
        "extra_tags": ["access", "visibility", "acl"],
    },
    "ae.support.diagnose_awaiting_input": {
        "title": "Support: Diagnose Awaiting Input",
        "description": "Explain why a request is blocked in Awaiting Input and identify the blocking task or human dependency.",
        "use_when": "The request is awaiting human input, approval, or task completion and you need the blocker summarized.",
        "avoid_when": "The request is in Failure or Running and not actually waiting on a manual task.",
        "input_examples": [{"request_id": "REQ-10562"}],
        "extra_tags": ["awaiting-input", "task-blocked", "human-in-the-loop"],
    },
    "ae.support.diagnose_agent_unavailable": {
        "title": "Support: Diagnose Agent Unavailable",
        "description": "Summarize why an AutomationEdge agent is unavailable, including state, connectivity, and workload clues.",
        "use_when": "A workflow cannot run because the target or assigned agent appears offline, unknown, or overloaded.",
        "avoid_when": "The issue is limited to a single request and the agent is already confirmed healthy.",
        "input_examples": [{"agent_id": "AE-AGENT-07"}],
        "extra_tags": ["agent-health", "connectivity", "capacity"],
    },
    "ae.support.diagnose_rdp_blocked_workflow": {
        "title": "Support: Diagnose RDP-Blocked Workflow",
        "description": "Check whether a workflow is blocked by RDP, locked desktop, or stale user-session conditions on the agent.",
        "use_when": "A desktop automation run appears blocked by UI session state or RDP-specific symptoms.",
        "avoid_when": "The workflow is API-only or there is no sign of desktop-session involvement.",
        "input_examples": [{"request_id": "REQ-10588"}],
        "extra_tags": ["rdp", "desktop", "ui-automation"],
    },
    "ae.support.build_case_snapshot": {
        "title": "Support: Build Escalation Snapshot",
        "description": "Assemble a support case snapshot with request summary, failing step, key timings, and optional log summary count.",
        "use_when": "You need a compact escalation package before handing the issue to another engineer or team.",
        "avoid_when": "You only need a quick diagnosis and not a case-quality snapshot artifact.",
        "input_examples": [{"request_id": "REQ-10421", "include_logs_summary": True}],
        "parameter_docs": {
            "include_logs_summary": "Whether to include a count-based summary of recent logs in the snapshot.",
        },
        "extra_tags": ["snapshot", "escalation", "handoff"],
    },
    "ae.support.prepare_human_handoff_note": {
        "title": "Support: Prepare Human Handoff Note",
        "description": "Generate a concise human-readable handoff note for support escalation or shift transfer.",
        "use_when": "You need a narrative update another human can read quickly without replaying the full investigation.",
        "avoid_when": "You still need raw evidence gathering rather than a summarized handoff note.",
        "input_examples": [{"request_id": "REQ-10421"}],
        "extra_tags": ["handoff-note", "shift-transfer", "summary"],
    },
}


def _humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in str(value or "").replace(".", " ").replace("_", " ").split())


def _default_title(tool_name: str) -> str:
    parts = str(tool_name or "").split(".")
    if len(parts) >= 3 and parts[0] == "ae":
        return f"{_humanize(parts[1])}: {_humanize(' '.join(parts[2:]))}"
    return _humanize(tool_name)


def _derive_tags(tool_name: str, mcp_category: str, safety: str) -> list[str]:
    raw_parts = []
    raw_parts.extend(str(tool_name or "").replace(".", "_").split("_"))
    raw_parts.extend(str(mcp_category or "").split("_"))
    raw_parts.append(str(safety or ""))

    seen: set[str] = set()
    tags: list[str] = []
    for part in raw_parts:
        clean = str(part or "").strip().lower()
        if not clean or clean in _TAG_STOPWORDS or clean in seen:
            continue
        seen.add(clean)
        tags.append(clean)
    return tags


def normalize_tool_result(raw_result: Any) -> dict[str, Any]:
    """Convert legacy JSON-string tool payloads into structured dict output."""
    if raw_result is None:
        return {}
    if isinstance(raw_result, dict):
        return raw_result
    if isinstance(raw_result, str):
        text = raw_result.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": raw_result}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    if isinstance(raw_result, list):
        return {"result": raw_result}
    return {"result": raw_result}


def serialize_annotations(annotations: ToolAnnotations | None) -> dict[str, Any]:
    if not annotations:
        return {}
    return annotations.model_dump(mode="json", by_alias=True, exclude_none=True)


def _make_structured_handler(handler: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    sig = inspect.signature(handler)
    typed_sig = sig.replace(return_annotation=dict[str, Any])

    @wraps(handler)
    async def _wrapper(*args, **kwargs) -> dict[str, Any]:
        return normalize_tool_result(await handler(*args, **kwargs))

    _wrapper.__name__ = f"{str(tool_name).replace('.', '_')}_structured"
    _wrapper.__qualname__ = _wrapper.__name__
    _wrapper.__signature__ = typed_sig
    annotations = dict(getattr(handler, "__annotations__", {}))
    annotations["return"] = dict[str, Any]
    _wrapper.__annotations__ = annotations
    return _wrapper


def _build_annotations(safety: str, title: str, app_category: str = "") -> ToolAnnotations:
    safe = str(safety or "").strip()
    return ToolAnnotations(
        title=title,
        readOnlyHint=safe == "safe_read",
        destructiveHint=safe in {"guarded", "privileged"},
        idempotentHint=safe == "safe_read",
        openWorldHint=True,
        appCategory=app_category,
    )


@dataclass
class MCPToolSpec:
    name: str
    handler: Callable[..., Any]
    mcp_category: str
    safety: str
    description: str = ""
    title: str = ""
    use_when: str = ""
    avoid_when: str = ""
    input_examples: list[dict[str, Any]] = field(default_factory=list)
    parameter_docs: dict[str, str] = field(default_factory=dict)
    extra_tags: list[str] = field(default_factory=list)
    latency_class: str = "medium"
    extra_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def always_available(self) -> bool:
        return self.name in _ALWAYS_AVAILABLE_MCP_TOOLS

    @property
    def app_category(self) -> str:
        return APP_CATEGORY_MAP.get(self.mcp_category, "status")

    @property
    def tier(self) -> str:
        return SAFETY_TO_TIER.get(self.safety, "read_only")

    @property
    def is_mutating(self) -> bool:
        return self.tier != "read_only"

    @property
    def resolved_title(self) -> str:
        return self.title or _default_title(self.name)

    @property
    def resolved_description(self) -> str:
        doc = inspect.getdoc(self.handler) or ""
        return self.description or doc or f"AutomationEdge tool {self.name}"

    @cached_property
    def annotations(self) -> ToolAnnotations:
        return _build_annotations(self.safety, self.resolved_title, self.app_category)

    @cached_property
    def tags(self) -> list[str]:
        tags = _derive_tags(self.name, self.mcp_category, self.safety)
        seen = set(tags)
        for tag in self.extra_tags:
            clean = str(tag or "").strip().lower()
            if clean and clean not in seen:
                tags.append(clean)
                seen.add(clean)
        return tags

    @cached_property
    def resolved_parameter_docs(self) -> dict[str, str]:
        docs = {}
        for name in (self._base_input_schema.get("properties", {}) or {}):
            if name in _COMMON_PARAMETER_DESCRIPTIONS:
                docs[name] = _COMMON_PARAMETER_DESCRIPTIONS[name]
        docs.update({k: v for k, v in self.parameter_docs.items() if v})
        return docs

    @cached_property
    def meta(self) -> dict[str, Any]:
        meta = {
            "source": "automationedge",
            "source_ref": self.name,
            "category": self.mcp_category,
            "app_category": self.app_category,
            "safety": self.safety,
            "tier": self.tier,
            "mutating": self.is_mutating,
            "always_available": self.always_available,
            "latency_class": self.latency_class,
            "structured_output": True,
            "tags": self.tags,
            "use_when": self.use_when,
            "avoid_when": self.avoid_when,
            "input_examples": self.input_examples[:2],
        }
        meta.update(self.extra_meta)
        return meta

    @cached_property
    def structured_handler(self) -> Callable[..., Any]:
        return _make_structured_handler(self.handler, self.name)

    @cached_property
    def fastmcp_tool(self) -> FastMCPTool:
        return FastMCPTool.from_function(
            self.structured_handler,
            name=self.name,
            description=self.resolved_description,
            annotations=self.annotations,
        )

    @cached_property
    def _base_input_schema(self) -> dict[str, Any]:
        return deepcopy(self.fastmcp_tool.parameters or {})

    @property
    def input_schema(self) -> dict[str, Any]:
        schema = deepcopy(self._base_input_schema)
        properties = schema.setdefault("properties", {})
        for param_name, description in self.resolved_parameter_docs.items():
            if param_name not in properties:
                continue
            updated = dict(properties[param_name] or {})
            updated["description"] = description
            properties[param_name] = updated
        if self.input_examples:
            schema["examples"] = deepcopy(self.input_examples[:2])
        return schema

    @property
    def parameter_properties(self) -> dict[str, Any]:
        return dict(self.input_schema.get("properties", {}) or {})

    @property
    def required_params(self) -> list[str]:
        return list(self.input_schema.get("required", []) or [])

    @property
    def output_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    @property
    def serialized_annotations(self) -> dict[str, Any]:
        return serialize_annotations(self.annotations)


def _spec(name: str, handler: Callable[..., Any], mcp_category: str, safety: str) -> MCPToolSpec:
    overrides = dict(_CURATED_TOOL_OVERRIDES.get(name, {}))
    return MCPToolSpec(
        name=name,
        handler=handler,
        mcp_category=mcp_category,
        safety=safety,
        **overrides,
    )


@lru_cache(maxsize=1)
def get_mcp_tool_specs() -> tuple[MCPToolSpec, ...]:
    from mcp_server.tools import agent_tools as _agent
    from mcp_server.tools import credential_tools as _cred
    from mcp_server.tools import dependency_tools as _dep
    from mcp_server.tools import misc_tools as _misc
    from mcp_server.tools import request_diag as _req_diag
    from mcp_server.tools import request_mutate as _req_mutate
    from mcp_server.tools import request_read as _req_read
    from mcp_server.tools import schedule_tools as _sched
    from mcp_server.tools import support_composite as _support
    from mcp_server.tools import task_tools as _task
    from mcp_server.tools import workflow_tools as _wf

    return (
        _spec("ae.request.get_by_id", _req_read.request_get_by_id, "request_read", "safe_read"),
        _spec("ae.request.get_status", _req_read.request_get_status, "request_read", "safe_read"),
        _spec("ae.request.get_summary", _req_read.request_get_summary, "request_read", "safe_read"),
        _spec("ae.request.search", _req_read.request_search, "request_read", "safe_read"),
        _spec("ae.request.list_for_user", _req_read.request_list_for_user, "request_read", "safe_read"),
        _spec("ae.request.list_for_workflow", _req_read.request_list_for_workflow, "request_read", "safe_read"),
        _spec("ae.request.list_by_status", _req_read.request_list_by_status, "request_read", "safe_read"),
        _spec("ae.request.list_stuck", _req_read.request_list_stuck, "request_read", "safe_read"),
        _spec("ae.request.list_failed_recently", _req_read.request_list_failed_recently, "request_read", "safe_read"),
        _spec("ae.request.list_retrying", _req_read.request_list_retrying, "request_read", "safe_read"),
        _spec("ae.request.list_awaiting_input", _req_read.request_list_awaiting_input, "request_read", "safe_read"),
        _spec("ae.request.get_input_parameters", _req_read.request_get_input_parameters, "request_read", "safe_read"),
        _spec("ae.request.get_failure_message", _req_read.request_get_failure_message, "request_read", "safe_read"),
        _spec("ae.request.build_support_snapshot", _req_read.request_build_support_snapshot, "request_read", "safe_read"),
        _spec("ae.request.list_recent", _req_read.request_list_recent, "request_read", "safe_read"),
        _spec("ae.request.get_logs", _req_read.request_get_logs, "request_read", "safe_read"),
        _spec("ae.request.get_source_context", _req_read.request_get_source_context, "request_read", "safe_read"),
        _spec("ae.request.get_time_details", _req_read.request_get_time_details, "request_read", "safe_read"),
        _spec("ae.request.get_execution_details", _req_diag.request_get_execution_details, "request_diag", "safe_read"),
        _spec("ae.request.get_audit_logs", _req_diag.request_get_audit_logs, "request_diag", "safe_read"),
        _spec("ae.request.get_step_logs", _req_diag.request_get_step_logs, "request_diag", "safe_read"),
        _spec("ae.request.get_live_progress", _req_diag.request_get_live_progress, "request_diag", "safe_read"),
        _spec("ae.request.get_last_error_step", _req_diag.request_get_last_error_step, "request_diag", "safe_read"),
        _spec("ae.request.get_manual_intervention_context", _req_diag.request_get_manual_intervention_context, "request_diag", "safe_read"),
        _spec("ae.request.get_last_successful_step", _req_diag.request_get_last_successful_step, "request_diag", "safe_read"),
        _spec("ae.request.compare_attempts", _req_diag.request_compare_attempts, "request_diag", "safe_read"),
        _spec("ae.request.export_diagnostic_bundle", _req_diag.request_export_diagnostic_bundle, "request_diag", "safe_read"),
        _spec("ae.request.generate_support_narrative", _req_diag.request_generate_support_narrative, "request_diag", "safe_read"),
        _spec("ae.request.restart", _req_mutate.request_restart, "request_mutate", "guarded"),
        _spec("ae.request.restart_failed", _req_mutate.request_restart_failed, "request_mutate", "guarded"),
        _spec("ae.request.terminate_running", _req_mutate.request_terminate_running, "request_mutate", "privileged"),
        _spec("ae.request.resubmit_from_failure_point", _req_mutate.request_resubmit_from_failure_point, "request_mutate", "guarded"),
        _spec("ae.request.add_support_comment", _req_mutate.request_add_support_comment, "request_mutate", "safe_mutation"),
        _spec("ae.request.cancel_new_or_retry", _req_mutate.request_cancel_new_or_retry, "request_mutate", "guarded"),
        _spec("ae.request.resubmit_from_start", _req_mutate.request_resubmit_from_start, "request_mutate", "guarded"),
        _spec("ae.request.tag_case_reference", _req_mutate.request_tag_case_reference, "request_mutate", "safe_mutation"),
        _spec("ae.request.raise_manual_handoff", _req_mutate.request_raise_manual_handoff, "request_mutate", "safe_mutation"),
        _spec("ae.workflow.search", _wf.workflow_search, "workflow_read", "safe_read"),
        _spec("ae.workflow.list", _wf.workflow_list, "workflow_read", "safe_read"),
        _spec("ae.workflow.list_for_user", _wf.workflow_list_for_user, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_details", _wf.workflow_get_details, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_runtime_parameters", _wf.workflow_get_runtime_parameters, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_flags", _wf.workflow_get_flags, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_assignment_targets", _wf.workflow_get_assignment_targets, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_permissions", _wf.workflow_get_permissions, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_by_id", _wf.workflow_get_by_id, "workflow_read", "safe_read"),
        _spec("ae.workflow.get_recent_failure_stats", _wf.workflow_get_recent_failure_stats, "workflow_read", "safe_read"),
        _spec("ae.workflow.enable", _wf.workflow_enable, "workflow_mutate", "guarded"),
        _spec("ae.workflow.disable", _wf.workflow_disable, "workflow_mutate", "guarded"),
        _spec("ae.workflow.assign_to_agent", _wf.workflow_assign_to_agent, "workflow_mutate", "guarded"),
        _spec("ae.workflow.update_permissions", _wf.workflow_update_permissions, "workflow_mutate", "privileged"),
        _spec("ae.workflow.rollback_version", _wf.workflow_rollback_version, "workflow_mutate", "privileged"),
        _spec("ae.agent.list_stopped", _agent.agent_list_stopped, "agent_read", "safe_read"),
        _spec("ae.agent.list_unknown", _agent.agent_list_unknown, "agent_read", "safe_read"),
        _spec("ae.agent.get_status", _agent.agent_get_status, "agent_read", "safe_read"),
        _spec("ae.agent.get_details", _agent.agent_get_details, "agent_read", "safe_read"),
        _spec("ae.agent.get_current_load", _agent.agent_get_current_load, "agent_read", "safe_read"),
        _spec("ae.agent.get_running_requests", _agent.agent_get_running_requests, "agent_read", "safe_read"),
        _spec("ae.agent.get_assigned_workflows", _agent.agent_get_assigned_workflows, "agent_read", "safe_read"),
        _spec("ae.agent.get_connectivity_state", _agent.agent_get_connectivity_state, "agent_read", "safe_read"),
        _spec("ae.agent.get_rdp_session_state", _agent.agent_get_rdp_session_state, "agent_read", "safe_read"),
        _spec("ae.agent.list_running", _agent.agent_list_running, "agent_read", "safe_read"),
        _spec("ae.agent.get_recent_failures", _agent.agent_get_recent_failures, "agent_read", "safe_read"),
        _spec("ae.agent.get_last_heartbeat", _agent.agent_get_last_heartbeat, "agent_read", "safe_read"),
        _spec("ae.agent.collect_diagnostics", _agent.agent_collect_diagnostics, "agent_read", "safe_read"),
        _spec("ae.agent.restart_service", _agent.agent_restart_service, "agent_mutate", "privileged"),
        _spec("ae.agent.clear_stale_rdp_session", _agent.agent_clear_stale_rdp_session, "agent_mutate", "privileged"),
        _spec("ae.schedule.list_all", _sched.schedule_list_all, "schedule_read", "safe_read"),
        _spec("ae.schedule.list_for_workflow", _sched.schedule_list_for_workflow, "schedule_read", "safe_read"),
        _spec("ae.schedule.get_details", _sched.schedule_get_details, "schedule_read", "safe_read"),
        _spec("ae.schedule.get_missed_runs", _sched.schedule_get_missed_runs, "schedule_read", "safe_read"),
        _spec("ae.schedule.get_recent_schedule_generated_requests", _sched.schedule_get_recent_generated_requests, "schedule_read", "safe_read"),
        _spec("ae.schedule.diagnose_not_triggered", _sched.schedule_diagnose_not_triggered, "schedule_read", "safe_read"),
        _spec("ae.schedule.get_next_runs", _sched.schedule_get_next_runs, "schedule_read", "safe_read"),
        _spec("ae.schedule.get_last_runs", _sched.schedule_get_last_runs, "schedule_read", "safe_read"),
        _spec("ae.schedule.enable", _sched.schedule_enable, "schedule_mutate", "guarded"),
        _spec("ae.schedule.run_now", _sched.schedule_run_now, "schedule_mutate", "guarded"),
        _spec("ae.schedule.disable", _sched.schedule_disable, "schedule_mutate", "guarded"),
        _spec("ae.task.get_request_context", _task.task_get_request_context, "task_read", "safe_read"),
        _spec("ae.task.list_blocking_requests", _task.task_list_blocking_requests, "task_read", "safe_read"),
        _spec("ae.task.search_pending", _task.task_search_pending, "task_read", "safe_read"),
        _spec("ae.task.get_assignees", _task.task_get_assignees, "task_read", "safe_read"),
        _spec("ae.task.get_overdue", _task.task_get_overdue, "task_read", "safe_read"),
        _spec("ae.task.cancel_admin", _task.task_cancel_admin, "task_read", "guarded"),
        _spec("ae.task.reassign", _task.task_reassign, "task_read", "guarded"),
        _spec("ae.task.explain_awaiting_input", _task.task_explain_awaiting_input, "task_read", "safe_read"),
        _spec("ae.credential_pool.get_availability", _cred.credential_pool_get_availability, "credential_read", "safe_read"),
        _spec("ae.credential_pool.get_waiting_requests", _cred.credential_pool_get_waiting_requests, "credential_read", "safe_read"),
        _spec("ae.credential_pool.diagnose_retry_state", _cred.credential_pool_diagnose_retry_state, "credential_read", "safe_read"),
        _spec("ae.credential_pool.validate_for_workflow", _cred.credential_pool_validate_for_workflow, "credential_read", "safe_read"),
        _spec("ae.dependency.check_input_file_exists", _dep.dependency_check_input_file_exists, "dependency", "safe_read"),
        _spec("ae.dependency.check_output_folder_writable", _dep.dependency_check_output_folder_writable, "dependency", "safe_read"),
        _spec("ae.dependency.run_full_preflight_for_workflow", _dep.dependency_run_full_preflight_for_workflow, "dependency", "safe_read"),
        _spec("ae.user.get_accessible_workflows", _misc.user_get_accessible_workflows, "user_read", "safe_read"),
        _spec("ae.permission.get_workflow_permissions", _misc.permission_get_workflow_permissions, "permission_read", "safe_read"),
        _spec("ae.permission.explain_user_access_issue", _misc.permission_explain_user_access_issue, "permission_read", "safe_read"),
        _spec("ae.platform.get_license_status", _misc.platform_get_license_status, "platform_read", "safe_read"),
        _spec("ae.platform.get_queue_depth", _misc.platform_get_queue_depth, "platform_read", "safe_read"),
        _spec("ae.result.get_failure_category", _misc.result_get_failure_category, "result_read", "safe_read"),
        _spec("ae.support.diagnose_failed_request", _support.diagnose_failed_request, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_stuck_running_request", _support.diagnose_stuck_running_request, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_retry_due_to_credentials", _support.diagnose_retry_due_to_credentials, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_no_output_file", _support.diagnose_no_output_file, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_schedule_not_triggered", _support.diagnose_schedule_not_triggered, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_user_cannot_find_workflow", _support.diagnose_user_cannot_find_workflow, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_awaiting_input", _support.diagnose_awaiting_input, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_agent_unavailable", _support.diagnose_agent_unavailable, "support_composite", "safe_read"),
        _spec("ae.support.diagnose_rdp_blocked_workflow", _support.diagnose_rdp_blocked_workflow, "support_composite", "safe_read"),
        _spec("ae.support.build_case_snapshot", _support.build_case_snapshot, "support_composite", "safe_read"),
        _spec("ae.support.prepare_human_handoff_note", _support.prepare_human_handoff_note, "support_composite", "safe_read"),
    )
