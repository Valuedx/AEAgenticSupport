"""
AutomationEdge MCP Server — registers all P0 tools via FastMCP.

71 tools across 16 categories:
  request_read (14), request_diag (6), request_mutate (4),
  workflow_read (7), workflow_mutate (4), agent_read (9), agent_mutate (2),
  schedule_read (5+1), schedule_mutate (1), task_read (2),
  credential_read (3), user_read (1), permission_read (2),
  platform_read (2), result_read (1), support_composite (8)
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from mcp_server.config import MCP_CONFIG

logger = logging.getLogger("ae_mcp.server")

mcp = FastMCP(
    "AutomationEdge Support",
    instructions=(
        "AutomationEdge IT Operations MCP Server. Provides tools for "
        "investigating, diagnosing, and remediating automation request issues, "
        "managing workflows, agents, schedules, credential pools, users, and "
        "permissions on the AutomationEdge platform. "
        "All mutating tools require a 'reason' parameter and support dry_run mode."
    ),
)

# ═══════════════════════════════════════════════════════════════════════
#  request_read  (14 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.request_read import (  # noqa: E402
    request_get_by_id,
    request_get_status,
    request_get_summary,
    request_search,
    request_list_for_user,
    request_list_for_workflow,
    request_list_by_status,
    request_list_stuck,
    request_list_failed_recently,
    request_list_retrying,
    request_list_awaiting_input,
    request_get_input_parameters,
    request_get_failure_message,
    request_build_support_snapshot,
    request_list_recent,
    request_get_source_context,
    request_get_time_details,
)


@mcp.tool(name="ae.request.get_by_id", description="Fetch full automation request record by ID")
async def _request_get_by_id(request_id: str) -> str:
    return await request_get_by_id(request_id)


@mcp.tool(name="ae.request.get_status", description="Fetch current request status with timestamps")
async def _request_get_status(request_id: str) -> str:
    return await request_get_status(request_id)


@mcp.tool(name="ae.request.get_summary", description="One-shot request support summary: workflow, user, agent, status, error, timing")
async def _request_get_summary(request_id: str) -> str:
    return await request_get_summary(request_id)


@mcp.tool(name="ae.request.search", description="Search requests by filters: workflow, user, status, agent, time range")
async def _request_search(
    workflow: str = "",
    user: str = "",
    status: str = "",
    agent: str = "",
    time_range_hours: int = 24,
    limit: int = 50,
) -> str:
    return await request_search(workflow, user, status, agent, time_range_hours, limit)


@mcp.tool(name="ae.request.list_for_user", description="Get all requests submitted by a specific user")
async def _request_list_for_user(user_id: str, limit: int = 50) -> str:
    return await request_list_for_user(user_id, limit)


@mcp.tool(name="ae.request.list_for_workflow", description="Get requests for a specific workflow within a time range")
async def _request_list_for_workflow(workflow_id: str, time_range_hours: int = 24, limit: int = 50) -> str:
    return await request_list_for_workflow(workflow_id, time_range_hours, limit)


@mcp.tool(name="ae.request.list_by_status", description="Fetch requests in a specific status (e.g. Failure, Retry, Running)")
async def _request_list_by_status(status: str, time_range_hours: int = 24, limit: int = 50) -> str:
    return await request_list_by_status(status, time_range_hours, limit)


@mcp.tool(name="ae.request.list_stuck", description="Detect stuck requests running longer than threshold minutes")
async def _request_list_stuck(threshold_minutes: int = 60, workflow: str = "", agent: str = "", limit: int = 50) -> str:
    return await request_list_stuck(threshold_minutes, workflow, agent, limit)


@mcp.tool(name="ae.request.list_failed_recently", description="List recently failed requests within a time window")
async def _request_list_failed_recently(time_range_hours: int = 24, workflow: str = "", limit: int = 50) -> str:
    return await request_list_failed_recently(time_range_hours, workflow, limit)


@mcp.tool(name="ae.request.list_retrying", description="List requests currently in Retry status")
async def _request_list_retrying(workflow: str = "", agent: str = "", limit: int = 50) -> str:
    return await request_list_retrying(workflow, agent, limit)


@mcp.tool(name="ae.request.list_awaiting_input", description="List requests blocked waiting for human input/approval")
async def _request_list_awaiting_input(workflow: str = "", limit: int = 50) -> str:
    return await request_list_awaiting_input(workflow, limit)


@mcp.tool(name="ae.request.get_input_parameters", description="Get runtime input parameters of a request (with optional sensitive data masking)")
async def _request_get_input_parameters(request_id: str, mask_sensitive: bool = True) -> str:
    return await request_get_input_parameters(request_id, mask_sensitive)


@mcp.tool(name="ae.request.get_failure_message", description="Fetch the latest failure/error message for a request")
async def _request_get_failure_message(request_id: str) -> str:
    return await request_get_failure_message(request_id)


@mcp.tool(name="ae.request.build_support_snapshot", description="Build a structured triage payload for a support case (aggregates request, steps, errors)")
async def _request_build_support_snapshot(request_id: str) -> str:
    return await request_build_support_snapshot(request_id)


@mcp.tool(name="ae.request.list_recent", description="List recent requests for support review")
async def _request_list_recent(limit: int = 50, workflow: str = "", status: str = "") -> str:
    return await request_list_recent(limit, workflow, status)


@mcp.tool(name="ae.request.get_source_context", description="Show trigger source: schedule, catalog, API")
async def _request_get_source_context(request_id: str) -> str:
    return await request_get_source_context(request_id)


@mcp.tool(name="ae.request.get_time_details", description="Timing breakdown: created, picked, completed, duration")
async def _request_get_time_details(request_id: str) -> str:
    return await request_get_time_details(request_id)


# ═══════════════════════════════════════════════════════════════════════
#  request_diag  (6 + 4 P1 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.request_diag import (  # noqa: E402
    request_get_execution_details,
    request_get_audit_logs,
    request_get_step_logs,
    request_get_live_progress,
    request_get_last_error_step,
    request_get_manual_intervention_context,
    request_get_last_successful_step,
    request_compare_attempts,
    request_export_diagnostic_bundle,
    request_generate_support_narrative,
)


@mcp.tool(name="ae.request.get_execution_details", description="Fetch full execution metadata including timing, params, output, retry info")
async def _request_get_execution_details(request_id: str) -> str:
    return await request_get_execution_details(request_id)


@mcp.tool(name="ae.request.get_audit_logs", description="Fetch the audit trail for a request (who did what)")
async def _request_get_audit_logs(request_id: str) -> str:
    return await request_get_audit_logs(request_id)


@mcp.tool(name="ae.request.get_step_logs", description="Fetch step-level execution logs to identify failing node")
async def _request_get_step_logs(request_id: str) -> str:
    return await request_get_step_logs(request_id)


@mcp.tool(name="ae.request.get_live_progress", description="Get current running state and active step for a live request")
async def _request_get_live_progress(request_id: str) -> str:
    return await request_get_live_progress(request_id)


@mcp.tool(name="ae.request.get_last_error_step", description="Identify the step where failure occurred for RCA")
async def _request_get_last_error_step(request_id: str) -> str:
    return await request_get_last_error_step(request_id)


@mcp.tool(name="ae.request.get_manual_intervention_context", description="Get HITL block details: task ID, assignee, pending fields")
async def _request_get_manual_intervention_context(request_id: str) -> str:
    return await request_get_manual_intervention_context(request_id)


@mcp.tool(name="ae.request.get_last_successful_step", description="Last successful step for resume planning")
async def _request_get_last_successful_step(request_id: str) -> str:
    return await request_get_last_successful_step(request_id)


@mcp.tool(name="ae.request.compare_attempts", description="Compare multiple attempts for the same logical request")
async def _request_compare_attempts(request_id: str) -> str:
    return await request_compare_attempts(request_id)


@mcp.tool(name="ae.request.export_diagnostic_bundle", description="Export case evidence for escalation")
async def _request_export_diagnostic_bundle(request_id: str) -> str:
    return await request_export_diagnostic_bundle(request_id)


@mcp.tool(name="ae.request.generate_support_narrative", description="Plain-language support summary for handoff")
async def _request_generate_support_narrative(request_id: str) -> str:
    return await request_generate_support_narrative(request_id)


# ═══════════════════════════════════════════════════════════════════════
#  request_mutate  (4 + 4 P1 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.request_mutate import (  # noqa: E402
    request_restart_failed,
    request_terminate_running,
    request_resubmit_from_failure_point,
    request_add_support_comment,
    request_cancel_new_or_retry,
    request_resubmit_from_start,
    request_tag_case_reference,
    request_raise_manual_handoff,
)


@mcp.tool(name="ae.request.restart_failed", description="[GUARDED] Restart a failed request with the same parameters. Requires reason.")
async def _request_restart_failed(
    request_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await request_restart_failed(request_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.request.terminate_running", description="[PRIVILEGED] Terminate an actively running request. Requires reason.")
async def _request_terminate_running(
    request_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await request_terminate_running(request_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.request.resubmit_from_failure_point", description="[GUARDED] Resume a request from its failure point. Requires reason.")
async def _request_resubmit_from_failure_point(
    request_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await request_resubmit_from_failure_point(request_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.request.add_support_comment", description="Add a support action note/comment to a request")
async def _request_add_support_comment(
    request_id: str, comment: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await request_add_support_comment(request_id, comment, requested_by, case_id, dry_run)


@mcp.tool(name="ae.request.cancel_new_or_retry", description="[GUARDED] Cancel a request that has not started (New or Retry)")
async def _request_cancel_new_or_retry(request_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False) -> str:
    return await request_cancel_new_or_retry(request_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.request.resubmit_from_start", description="[GUARDED] Resubmit request from the beginning")
async def _request_resubmit_from_start(request_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False) -> str:
    return await request_resubmit_from_start(request_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.request.tag_case_reference", description="Link request to a support case")
async def _request_tag_case_reference(request_id: str, case_id: str, requested_by: str = "", dry_run: bool = False) -> str:
    return await request_tag_case_reference(request_id, case_id, requested_by, dry_run)


@mcp.tool(name="ae.request.raise_manual_handoff", description="Mark request for human handling")
async def _request_raise_manual_handoff(request_id: str, comment: str = "", requested_by: str = "", case_id: str = "", dry_run: bool = False) -> str:
    return await request_raise_manual_handoff(request_id, comment, requested_by, case_id, dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  workflow_read  (7 + 2 P1 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.workflow_tools import (  # noqa: E402
    workflow_search,
    workflow_list_for_user,
    workflow_get_details,
    workflow_get_runtime_parameters,
    workflow_get_flags,
    workflow_get_assignment_targets,
    workflow_get_permissions,
    workflow_get_by_id,
    workflow_get_recent_failure_stats,
    workflow_enable,
)


@mcp.tool(name="ae.workflow.search", description="Search workflows by name, description, or category")
async def _workflow_search(query: str = "", category: str = "", limit: int = 50) -> str:
    return await workflow_search(query, category, limit)


@mcp.tool(name="ae.workflow.list_for_user", description="Get workflows visible and accessible to a specific user")
async def _workflow_list_for_user(user_id: str) -> str:
    return await workflow_list_for_user(user_id)


@mcp.tool(name="ae.workflow.get_details", description="Get full workflow configuration details")
async def _workflow_get_details(workflow_id: str) -> str:
    return await workflow_get_details(workflow_id)


@mcp.tool(name="ae.workflow.get_runtime_parameters", description="Get the input parameter schema for a workflow")
async def _workflow_get_runtime_parameters(workflow_id: str) -> str:
    return await workflow_get_runtime_parameters(workflow_id)


@mcp.tool(name="ae.workflow.get_flags", description="Get monitoring, checkpoint, retry, and logging flags for a workflow")
async def _workflow_get_flags(workflow_id: str) -> str:
    return await workflow_get_flags(workflow_id)


@mcp.tool(name="ae.workflow.get_assignment_targets", description="Get assigned agents and controllers for a workflow")
async def _workflow_get_assignment_targets(workflow_id: str) -> str:
    return await workflow_get_assignment_targets(workflow_id)


@mcp.tool(name="ae.workflow.get_permissions", description="Get permission configuration for a workflow")
async def _workflow_get_permissions(workflow_id: str) -> str:
    return await workflow_get_permissions(workflow_id)


@mcp.tool(name="ae.workflow.get_by_id", description="Fetch workflow by ID for support context")
async def _workflow_get_by_id(workflow_id: str) -> str:
    return await workflow_get_by_id(workflow_id)


@mcp.tool(name="ae.workflow.get_recent_failure_stats", description="Recent failure counts and last failure for a workflow")
async def _workflow_get_recent_failure_stats(workflow_id: str, time_range_hours: int = 24) -> str:
    return await workflow_get_recent_failure_stats(workflow_id, time_range_hours)


@mcp.tool(name="ae.workflow.enable", description="[GUARDED] Re-enable a disabled workflow. Requires reason.")
async def _workflow_enable(
    workflow_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await workflow_enable(workflow_id, reason, requested_by, case_id, dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  workflow_mutate  (4 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.workflow_tools import (  # noqa: E402
    workflow_disable,
    workflow_assign_to_agent,
    workflow_update_permissions,
    workflow_rollback_version,
)


@mcp.tool(name="ae.workflow.disable", description="[GUARDED] Disable a workflow. Requires reason.")
async def _workflow_disable(
    workflow_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await workflow_disable(workflow_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.workflow.assign_to_agent", description="[GUARDED] Assign a workflow to a specific agent. Requires reason.")
async def _workflow_assign_to_agent(
    workflow_id: str, agent_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await workflow_assign_to_agent(workflow_id, agent_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.workflow.update_permissions", description="[PRIVILEGED] Update access control permissions for a workflow. Requires reason.")
async def _workflow_update_permissions(
    workflow_id: str, permissions: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await workflow_update_permissions(workflow_id, permissions, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.workflow.rollback_version", description="[PRIVILEGED] Rollback workflow to a previous version. Requires reason.")
async def _workflow_rollback_version(
    workflow_id: str, version: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await workflow_rollback_version(workflow_id, version, reason, requested_by, case_id, dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  agent_read  (9 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.agent_tools import (  # noqa: E402
    agent_list_stopped,
    agent_list_unknown,
    agent_get_status,
    agent_get_details,
    agent_get_current_load,
    agent_get_running_requests,
    agent_get_assigned_workflows,
    agent_get_connectivity_state,
    agent_get_rdp_session_state,
    agent_list_running,
    agent_get_recent_failures,
    agent_get_last_heartbeat,
    agent_collect_diagnostics,
)


@mcp.tool(name="ae.agent.list_stopped", description="List all agents currently in Stopped/Disconnected state")
async def _agent_list_stopped() -> str:
    return await agent_list_stopped()


@mcp.tool(name="ae.agent.list_unknown", description="List agents in unknown or unrecognized state")
async def _agent_list_unknown() -> str:
    return await agent_list_unknown()


@mcp.tool(name="ae.agent.get_status", description="Get current status and health of a specific agent")
async def _agent_get_status(agent_id: str) -> str:
    return await agent_get_status(agent_id)


@mcp.tool(name="ae.agent.get_details", description="Get full details of an agent including version, OS, config")
async def _agent_get_details(agent_id: str) -> str:
    return await agent_get_details(agent_id)


@mcp.tool(name="ae.agent.get_current_load", description="Get active workload summary for an agent: running requests, capacity")
async def _agent_get_current_load(agent_id: str) -> str:
    return await agent_get_current_load(agent_id)


@mcp.tool(name="ae.agent.get_running_requests", description="Get requests currently executing on a specific agent")
async def _agent_get_running_requests(agent_id: str, limit: int = 50) -> str:
    return await agent_get_running_requests(agent_id, limit)


@mcp.tool(name="ae.agent.get_assigned_workflows", description="Get workflows assigned to a specific agent")
async def _agent_get_assigned_workflows(agent_id: str) -> str:
    return await agent_get_assigned_workflows(agent_id)


@mcp.tool(name="ae.agent.get_connectivity_state", description="Check agent connectivity to controller and platform")
async def _agent_get_connectivity_state(agent_id: str) -> str:
    return await agent_get_connectivity_state(agent_id)


@mcp.tool(name="ae.agent.get_rdp_session_state", description="Check RDP/desktop session state for an agent")
async def _agent_get_rdp_session_state(agent_id: str) -> str:
    return await agent_get_rdp_session_state(agent_id)


@mcp.tool(name="ae.agent.list_running", description="List agents currently running and their load")
async def _agent_list_running() -> str:
    return await agent_list_running()


@mcp.tool(name="ae.agent.get_recent_failures", description="Recent request failures for an agent")
async def _agent_get_recent_failures(agent_id: str, time_range_hours: int = 24, limit: int = 20) -> str:
    return await agent_get_recent_failures(agent_id, time_range_hours, limit)


@mcp.tool(name="ae.agent.get_last_heartbeat", description="Last heartbeat and connectivity for an agent")
async def _agent_get_last_heartbeat(agent_id: str) -> str:
    return await agent_get_last_heartbeat(agent_id)


@mcp.tool(name="ae.agent.collect_diagnostics", description="Collect agent diagnostics for support")
async def _agent_collect_diagnostics(agent_id: str) -> str:
    return await agent_collect_diagnostics(agent_id)


# ═══════════════════════════════════════════════════════════════════════
#  agent_mutate  (2 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.agent_tools import (  # noqa: E402
    agent_restart_service,
    agent_clear_stale_rdp_session,
)


@mcp.tool(name="ae.agent.restart_service", description="[PRIVILEGED] Restart the AE agent service. Requires reason.")
async def _agent_restart_service(
    agent_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await agent_restart_service(agent_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.agent.clear_stale_rdp_session", description="[PRIVILEGED] Clear a stuck RDP desktop session on an agent. Requires reason.")
async def _agent_clear_stale_rdp_session(
    agent_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await agent_clear_stale_rdp_session(agent_id, reason, requested_by, case_id, dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  schedule_read  (5 + 1 diagnose)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.schedule_tools import (  # noqa: E402
    schedule_list_for_workflow,
    schedule_get_details,
    schedule_get_missed_runs,
    schedule_get_recent_generated_requests,
    schedule_diagnose_not_triggered,
    schedule_get_next_runs,
    schedule_get_last_runs,
    schedule_enable,
    schedule_run_now,
)


@mcp.tool(name="ae.schedule.list_for_workflow", description="Get schedules linked to a specific workflow")
async def _schedule_list_for_workflow(workflow_id: str) -> str:
    return await schedule_list_for_workflow(workflow_id)


@mcp.tool(name="ae.schedule.get_details", description="Get full schedule configuration and timing details")
async def _schedule_get_details(schedule_id: str) -> str:
    return await schedule_get_details(schedule_id)


@mcp.tool(name="ae.schedule.get_missed_runs", description="Detect missed scheduled runs within a time range")
async def _schedule_get_missed_runs(schedule_id: str, time_range_hours: int = 24) -> str:
    return await schedule_get_missed_runs(schedule_id, time_range_hours)


@mcp.tool(name="ae.schedule.get_recent_schedule_generated_requests", description="Map a schedule to the requests it recently generated")
async def _schedule_get_recent_generated_requests(schedule_id: str, time_range_hours: int = 24) -> str:
    return await schedule_get_recent_generated_requests(schedule_id, time_range_hours)


@mcp.tool(name="ae.schedule.diagnose_not_triggered", description="One-shot diagnosis of why a schedule didn't trigger")
async def _schedule_diagnose_not_triggered(schedule_id: str) -> str:
    return await schedule_diagnose_not_triggered(schedule_id)


@mcp.tool(name="ae.schedule.get_next_runs", description="Upcoming scheduled run times")
async def _schedule_get_next_runs(schedule_id: str) -> str:
    return await schedule_get_next_runs(schedule_id)


@mcp.tool(name="ae.schedule.get_last_runs", description="Last run times and outcomes for a schedule")
async def _schedule_get_last_runs(schedule_id: str, limit: int = 10) -> str:
    return await schedule_get_last_runs(schedule_id, limit)


@mcp.tool(name="ae.schedule.enable", description="[GUARDED] Re-enable a disabled schedule. Requires reason.")
async def _schedule_enable(
    schedule_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await schedule_enable(schedule_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.schedule.run_now", description="[GUARDED] Trigger a schedule run immediately")
async def _schedule_run_now(
    schedule_id: str, reason: str = "", requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await schedule_run_now(schedule_id, reason, requested_by, case_id, dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  schedule_mutate  (1 tool)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.schedule_tools import schedule_disable  # noqa: E402


@mcp.tool(name="ae.schedule.disable", description="[GUARDED] Disable a schedule to stop triggering. Requires reason.")
async def _schedule_disable(
    schedule_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await schedule_disable(schedule_id, reason, requested_by, case_id, dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  task_read  (2 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.task_tools import (  # noqa: E402
    task_get_request_context,
    task_list_blocking_requests,
    task_search_pending,
    task_get_assignees,
    task_get_overdue,
    task_cancel_admin,
    task_reassign,
    task_explain_awaiting_input,
)


@mcp.tool(name="ae.task.get_request_context", description="Map a task to its parent request and workflow context")
async def _task_get_request_context(task_id: str) -> str:
    return await task_get_request_context(task_id)


@mcp.tool(name="ae.task.list_blocking_requests", description="List requests blocked by pending tasks (Awaiting Input queue)")
async def _task_list_blocking_requests(workflow: str = "", limit: int = 50) -> str:
    return await task_list_blocking_requests(workflow, limit)


@mcp.tool(name="ae.task.search_pending", description="Search pending tasks by workflow, assignee, or age")
async def _task_search_pending(workflow: str = "", limit: int = 50) -> str:
    return await task_search_pending(workflow, limit)


@mcp.tool(name="ae.task.get_assignees", description="List users who can be assigned to a task")
async def _task_get_assignees(task_id: str) -> str:
    return await task_get_assignees(task_id)


@mcp.tool(name="ae.task.get_overdue", description="List overdue tasks for a workflow or assignee")
async def _task_get_overdue(limit: int = 50) -> str:
    return await task_get_overdue(limit)


@mcp.tool(name="ae.task.cancel_admin", description="[GUARDED] Cancel a task (admin). Requires reason.")
async def _task_cancel_admin(
    task_id: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await task_cancel_admin(task_id, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.task.reassign", description="[GUARDED] Reassign a task to another user. Requires reason.")
async def _task_reassign(
    task_id: str, target_user_or_group: str, reason: str, requested_by: str = "", case_id: str = "", dry_run: bool = False
) -> str:
    return await task_reassign(task_id, target_user_or_group, reason, requested_by, case_id, dry_run)


@mcp.tool(name="ae.task.explain_awaiting_input", description="Explain why a request is awaiting input and what is needed")
async def _task_explain_awaiting_input(request_id: str) -> str:
    return await task_explain_awaiting_input(request_id)


# ═══════════════════════════════════════════════════════════════════════
#  credential_read  (3 + 1 P1 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.credential_tools import (  # noqa: E402
    credential_pool_get_availability,
    credential_pool_get_waiting_requests,
    credential_pool_diagnose_retry_state,
    credential_pool_validate_for_workflow,
)


@mcp.tool(name="ae.credential_pool.get_availability", description="Check available vs in-use credentials in a pool")
async def _credential_pool_get_availability(pool_id: str) -> str:
    return await credential_pool_get_availability(pool_id)


@mcp.tool(name="ae.credential_pool.get_waiting_requests", description="Get requests waiting on a credential pool")
async def _credential_pool_get_waiting_requests(pool_id: str) -> str:
    return await credential_pool_get_waiting_requests(pool_id)


@mcp.tool(name="ae.credential_pool.diagnose_retry_state", description="One-shot diagnosis of credential-related Retry state")
async def _credential_pool_diagnose_retry_state(request_id: str = "", pool_id: str = "") -> str:
    return await credential_pool_diagnose_retry_state(request_id, pool_id)


@mcp.tool(name="ae.credential_pool.validate_for_workflow", description="Validate credential pool configuration for a workflow")
async def _credential_pool_validate_for_workflow(workflow_id: str, pool_id: str = "") -> str:
    return await credential_pool_validate_for_workflow(workflow_id, pool_id)


# ═══════════════════════════════════════════════════════════════════════
#  dependency  (3 P1 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.dependency_tools import (  # noqa: E402
    dependency_check_input_file_exists,
    dependency_check_output_folder_writable,
    dependency_run_full_preflight_for_workflow,
)


@mcp.tool(name="ae.dependency.check_input_file_exists", description="Check if an input file/path exists for a workflow run")
async def _dependency_check_input_file_exists(workflow_id: str, request_id: str = "", path_param: str = "") -> str:
    return await dependency_check_input_file_exists(workflow_id, request_id, path_param)


@mcp.tool(name="ae.dependency.check_output_folder_writable", description="Check if output folder is writable for a workflow")
async def _dependency_check_output_folder_writable(workflow_id: str, path_param: str = "") -> str:
    return await dependency_check_output_folder_writable(workflow_id, path_param)


@mcp.tool(name="ae.dependency.run_full_preflight_for_workflow", description="Run full preflight checks for a workflow (files, creds, agent)")
async def _dependency_run_full_preflight_for_workflow(workflow_id: str, request_id: str = "") -> str:
    return await dependency_run_full_preflight_for_workflow(workflow_id, request_id)


# ═══════════════════════════════════════════════════════════════════════
#  user_read (1), permission_read (2), platform_read (2), result_read (1)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.misc_tools import (  # noqa: E402
    user_get_accessible_workflows,
    permission_get_workflow_permissions,
    permission_explain_user_access_issue,
    platform_get_license_status,
    platform_get_queue_depth,
    result_get_failure_category,
)


@mcp.tool(name="ae.user.get_accessible_workflows", description="Get workflows accessible to a user")
async def _user_get_accessible_workflows(user_id: str) -> str:
    return await user_get_accessible_workflows(user_id)


@mcp.tool(name="ae.permission.get_workflow_permissions", description="Get the permission map for a workflow")
async def _permission_get_workflow_permissions(workflow_id: str) -> str:
    return await permission_get_workflow_permissions(workflow_id)


@mcp.tool(name="ae.permission.explain_user_access_issue", description="One-shot diagnosis of why a user cannot see or run a workflow")
async def _permission_explain_user_access_issue(user_id: str, workflow_id: str) -> str:
    return await permission_explain_user_access_issue(user_id, workflow_id)


@mcp.tool(name="ae.platform.get_license_status", description="Get current platform license state")
async def _platform_get_license_status(tenant_id: str = "") -> str:
    return await platform_get_license_status(tenant_id)


@mcp.tool(name="ae.platform.get_queue_depth", description="Get queue depth summary across the platform")
async def _platform_get_queue_depth(tenant_id: str = "") -> str:
    return await platform_get_queue_depth(tenant_id)


@mcp.tool(name="ae.result.get_failure_category", description="Classify a request failure into a normalized category (CREDENTIAL, TIMEOUT, CONNECTIVITY, etc.)")
async def _result_get_failure_category(request_id: str) -> str:
    return await result_get_failure_category(request_id)


# ═══════════════════════════════════════════════════════════════════════
#  support_composite  (8 tools)
# ═══════════════════════════════════════════════════════════════════════

from mcp_server.tools.support_composite import (  # noqa: E402
    diagnose_failed_request,
    diagnose_stuck_running_request,
    diagnose_retry_due_to_credentials,
    diagnose_no_output_file,
    diagnose_schedule_not_triggered as _diag_schedule,
    diagnose_user_cannot_find_workflow,
    diagnose_awaiting_input,
    diagnose_agent_unavailable,
    diagnose_rdp_blocked_workflow,
    build_case_snapshot,
    prepare_human_handoff_note,
)


@mcp.tool(name="ae.support.diagnose_failed_request", description="One-shot diagnosis of a failed request: error classification, failing step, recommendations")
async def _diagnose_failed_request(request_id: str) -> str:
    return await diagnose_failed_request(request_id)


@mcp.tool(name="ae.support.diagnose_stuck_running_request", description="Diagnose a request that appears hung in Running state")
async def _diagnose_stuck_running_request(request_id: str) -> str:
    return await diagnose_stuck_running_request(request_id)


@mcp.tool(name="ae.support.diagnose_retry_due_to_credentials", description="Diagnose a Retry state caused by credential pool exhaustion")
async def _diagnose_retry_due_to_credentials(request_id: str) -> str:
    return await diagnose_retry_due_to_credentials(request_id)


@mcp.tool(name="ae.support.diagnose_no_output_file", description="Diagnose why a completed request produced no output file/artifact")
async def _diagnose_no_output_file(request_id: str) -> str:
    return await diagnose_no_output_file(request_id)


@mcp.tool(name="ae.support.diagnose_schedule_not_triggered", description="Diagnose why a scheduled job never started")
async def _diagnose_schedule_not_triggered(schedule_id: str) -> str:
    return await _diag_schedule(schedule_id)


@mcp.tool(name="ae.support.diagnose_user_cannot_find_workflow", description="Diagnose why a user cannot see or access a specific workflow")
async def _diagnose_user_cannot_find_workflow(user_id: str, workflow_id: str) -> str:
    return await diagnose_user_cannot_find_workflow(user_id, workflow_id)


@mcp.tool(name="ae.support.diagnose_awaiting_input", description="Diagnose why a request is blocked in Awaiting Input state")
async def _diagnose_awaiting_input(request_id: str) -> str:
    return await diagnose_awaiting_input(request_id)


@mcp.tool(name="ae.support.diagnose_agent_unavailable", description="Diagnose why an agent is unavailable: state, connectivity, load")
async def _diagnose_agent_unavailable(agent_id: str) -> str:
    return await diagnose_agent_unavailable(agent_id)


@mcp.tool(name="ae.support.diagnose_rdp_blocked_workflow", description="Diagnose workflow blocked by RDP/session state on agent")
async def _diagnose_rdp_blocked_workflow(request_id: str) -> str:
    return await diagnose_rdp_blocked_workflow(request_id)


@mcp.tool(name="ae.support.build_case_snapshot", description="Build a full case snapshot for escalation")
async def _build_case_snapshot(request_id: str) -> str:
    return await build_case_snapshot(request_id)


@mcp.tool(name="ae.support.prepare_human_handoff_note", description="Generate human-readable handoff note for support")
async def _prepare_human_handoff_note(request_id: str) -> str:
    return await prepare_human_handoff_note(request_id)
