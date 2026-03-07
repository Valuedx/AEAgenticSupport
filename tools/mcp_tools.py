"""
Register AutomationEdge MCP P0 tools with the main app tool registry.

When AE_MCP_TOOLS_ENABLED is true, P0 + support-priority P1 tool implementations
from mcp_server are registered so the orchestrator and specialists can use them.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import logging
from typing import Any, Callable

from config.settings import CONFIG
from tools.base import ToolDefinition, ToolResult
from tools.catalog import ToolCatalogEntry
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.mcp_tools")

# Map MCP catalog category -> main app category (for agent filtering)
_CATEGORY_MAP = {
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
_SAFETY_TO_TIER = {
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
}

# Shared executor for MCP async tool calls (avoids per-call executor creation)
_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_mcp_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp_tool")
    return _executor


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = _get_mcp_executor().submit(asyncio.run, coro)
            return future.result()
        return asyncio.run(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _run_mcp_tool(tool_name: str, async_fn: Callable, **kwargs: Any) -> ToolResult:
    sig = inspect.signature(async_fn)
    param_names = set(sig.parameters)
    filtered = {k: v for k, v in kwargs.items() if k in param_names and v is not None}
    for k, v in kwargs.items():
        if k in param_names and k not in filtered and v is None:
            filtered[k] = v
    try:
        result_str = _run_async(async_fn(**filtered))
    except Exception as exc:
        logger.exception("MCP tool %s failed", tool_name)
        return ToolResult(success=False, error=str(exc), tool_name=tool_name)
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
    except (TypeError, json.JSONDecodeError):
        data = {"raw": result_str}
    if isinstance(data, dict) and "error" in data and data.get("error"):
        return ToolResult(success=False, data=data, error=data["error"], tool_name=tool_name)
    return ToolResult(success=True, data=data, tool_name=tool_name)


def _register_mcp_tools() -> None:
    if not CONFIG.get("AE_MCP_TOOLS_ENABLED", False):
        return

    try:
        from mcp_server.tools import request_read as _req_read
        from mcp_server.tools import request_diag as _req_diag
        from mcp_server.tools import request_mutate as _req_mutate
        from mcp_server.tools import workflow_tools as _wf
        from mcp_server.tools import agent_tools as _agent
        from mcp_server.tools import schedule_tools as _sched
        from mcp_server.tools import task_tools as _task
        from mcp_server.tools import credential_tools as _cred
        from mcp_server.tools import dependency_tools as _dep
        from mcp_server.tools import misc_tools as _misc
        from mcp_server.tools import support_composite as _support
    except ImportError as e:
        logger.warning("MCP tools not available (mcp_server not installed or path): %s", e)
        return

    dispatch: dict[str, tuple[Callable, str, str, list[str], dict]] = {}
    # request_read (14)
    _add(dispatch, "ae.request.get_by_id", _req_read.request_get_by_id, "request_read", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_status", _req_read.request_get_status, "request_read", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_summary", _req_read.request_get_summary, "request_read", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.search", _req_read.request_search, "request_read", "safe_read", [], {"workflow": "string", "user": "string", "status": "string", "agent": "string", "time_range_hours": "integer", "limit": "integer"})
    _add(dispatch, "ae.request.list_for_user", _req_read.request_list_for_user, "request_read", "safe_read", ["user_id"], {"limit": "integer"})
    _add(dispatch, "ae.request.list_for_workflow", _req_read.request_list_for_workflow, "request_read", "safe_read", ["workflow_id"], {"time_range_hours": "integer", "limit": "integer"})
    _add(dispatch, "ae.request.list_by_status", _req_read.request_list_by_status, "request_read", "safe_read", ["status"], {"time_range_hours": "integer", "limit": "integer"})
    _add(dispatch, "ae.request.list_stuck", _req_read.request_list_stuck, "request_read", "safe_read", [], {"threshold_minutes": "integer", "workflow": "string", "agent": "string", "limit": "integer"})
    _add(dispatch, "ae.request.list_failed_recently", _req_read.request_list_failed_recently, "request_read", "safe_read", [], {"time_range_hours": "integer", "workflow": "string", "limit": "integer"})
    _add(dispatch, "ae.request.list_retrying", _req_read.request_list_retrying, "request_read", "safe_read", [], {"workflow": "string", "agent": "string", "limit": "integer"})
    _add(dispatch, "ae.request.list_awaiting_input", _req_read.request_list_awaiting_input, "request_read", "safe_read", [], {"workflow": "string", "limit": "integer"})
    _add(dispatch, "ae.request.get_input_parameters", _req_read.request_get_input_parameters, "request_read", "safe_read", ["request_id"], {"mask_sensitive": "boolean"})
    _add(dispatch, "ae.request.get_failure_message", _req_read.request_get_failure_message, "request_read", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.build_support_snapshot", _req_read.request_build_support_snapshot, "request_read", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.list_recent", _req_read.request_list_recent, "request_read", "safe_read", [], {"limit": "integer", "workflow": "string", "status": "string"})
    _add(dispatch, "ae.request.get_source_context", _req_read.request_get_source_context, "request_read", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_time_details", _req_read.request_get_time_details, "request_read", "safe_read", ["request_id"], {})
    # request_diag (6 + 4 P1)
    _add(dispatch, "ae.request.get_execution_details", _req_diag.request_get_execution_details, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_audit_logs", _req_diag.request_get_audit_logs, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_step_logs", _req_diag.request_get_step_logs, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_live_progress", _req_diag.request_get_live_progress, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_last_error_step", _req_diag.request_get_last_error_step, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_manual_intervention_context", _req_diag.request_get_manual_intervention_context, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.get_last_successful_step", _req_diag.request_get_last_successful_step, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.compare_attempts", _req_diag.request_compare_attempts, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.export_diagnostic_bundle", _req_diag.request_export_diagnostic_bundle, "request_diag", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.request.generate_support_narrative", _req_diag.request_generate_support_narrative, "request_diag", "safe_read", ["request_id"], {})
    # request_mutate (4 + 4 P1)
    _add(dispatch, "ae.request.restart_failed", _req_mutate.request_restart_failed, "request_mutate", "guarded", ["request_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.terminate_running", _req_mutate.request_terminate_running, "request_mutate", "privileged", ["request_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.resubmit_from_failure_point", _req_mutate.request_resubmit_from_failure_point, "request_mutate", "guarded", ["request_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.add_support_comment", _req_mutate.request_add_support_comment, "request_mutate", "safe_mutation", ["request_id", "comment"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.cancel_new_or_retry", _req_mutate.request_cancel_new_or_retry, "request_mutate", "guarded", ["request_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.resubmit_from_start", _req_mutate.request_resubmit_from_start, "request_mutate", "guarded", ["request_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.tag_case_reference", _req_mutate.request_tag_case_reference, "request_mutate", "safe_mutation", ["request_id", "case_id"], {"requested_by": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.request.raise_manual_handoff", _req_mutate.request_raise_manual_handoff, "request_mutate", "safe_mutation", ["request_id"], {"comment": "string", "requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    # workflow (11 + 3 P1)
    _add(dispatch, "ae.workflow.search", _wf.workflow_search, "workflow_read", "safe_read", [], {"query": "string", "category": "string", "limit": "integer"})
    _add(dispatch, "ae.workflow.list_for_user", _wf.workflow_list_for_user, "workflow_read", "safe_read", ["user_id"], {})
    _add(dispatch, "ae.workflow.get_details", _wf.workflow_get_details, "workflow_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.workflow.get_runtime_parameters", _wf.workflow_get_runtime_parameters, "workflow_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.workflow.get_flags", _wf.workflow_get_flags, "workflow_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.workflow.get_assignment_targets", _wf.workflow_get_assignment_targets, "workflow_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.workflow.get_permissions", _wf.workflow_get_permissions, "workflow_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.workflow.get_by_id", _wf.workflow_get_by_id, "workflow_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.workflow.get_recent_failure_stats", _wf.workflow_get_recent_failure_stats, "workflow_read", "safe_read", ["workflow_id"], {"time_range_hours": "integer"})
    _add(dispatch, "ae.workflow.enable", _wf.workflow_enable, "workflow_mutate", "guarded", ["workflow_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.workflow.disable", _wf.workflow_disable, "workflow_mutate", "guarded", ["workflow_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.workflow.assign_to_agent", _wf.workflow_assign_to_agent, "workflow_mutate", "guarded", ["workflow_id", "agent_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.workflow.update_permissions", _wf.workflow_update_permissions, "workflow_mutate", "privileged", ["workflow_id", "permissions", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.workflow.rollback_version", _wf.workflow_rollback_version, "workflow_mutate", "privileged", ["workflow_id", "version", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    # agent (11)
    _add(dispatch, "ae.agent.list_stopped", _agent.agent_list_stopped, "agent_read", "safe_read", [], {})
    _add(dispatch, "ae.agent.list_unknown", _agent.agent_list_unknown, "agent_read", "safe_read", [], {})
    _add(dispatch, "ae.agent.get_status", _agent.agent_get_status, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.get_details", _agent.agent_get_details, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.get_current_load", _agent.agent_get_current_load, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.get_running_requests", _agent.agent_get_running_requests, "agent_read", "safe_read", ["agent_id"], {"limit": "integer"})
    _add(dispatch, "ae.agent.get_assigned_workflows", _agent.agent_get_assigned_workflows, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.get_connectivity_state", _agent.agent_get_connectivity_state, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.get_rdp_session_state", _agent.agent_get_rdp_session_state, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.list_running", _agent.agent_list_running, "agent_read", "safe_read", [], {})
    _add(dispatch, "ae.agent.get_recent_failures", _agent.agent_get_recent_failures, "agent_read", "safe_read", ["agent_id"], {"time_range_hours": "integer", "limit": "integer"})
    _add(dispatch, "ae.agent.get_last_heartbeat", _agent.agent_get_last_heartbeat, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.collect_diagnostics", _agent.agent_collect_diagnostics, "agent_read", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.agent.restart_service", _agent.agent_restart_service, "agent_mutate", "privileged", ["agent_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.agent.clear_stale_rdp_session", _agent.agent_clear_stale_rdp_session, "agent_mutate", "privileged", ["agent_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    # schedule (7)
    _add(dispatch, "ae.schedule.list_for_workflow", _sched.schedule_list_for_workflow, "schedule_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.schedule.get_details", _sched.schedule_get_details, "schedule_read", "safe_read", ["schedule_id"], {})
    _add(dispatch, "ae.schedule.get_missed_runs", _sched.schedule_get_missed_runs, "schedule_read", "safe_read", ["schedule_id"], {"time_range_hours": "integer"})
    _add(dispatch, "ae.schedule.get_recent_schedule_generated_requests", _sched.schedule_get_recent_generated_requests, "schedule_read", "safe_read", ["schedule_id"], {"time_range_hours": "integer"})
    _add(dispatch, "ae.schedule.diagnose_not_triggered", _sched.schedule_diagnose_not_triggered, "schedule_read", "safe_read", ["schedule_id"], {})
    _add(dispatch, "ae.schedule.get_next_runs", _sched.schedule_get_next_runs, "schedule_read", "safe_read", ["schedule_id"], {})
    _add(dispatch, "ae.schedule.get_last_runs", _sched.schedule_get_last_runs, "schedule_read", "safe_read", ["schedule_id"], {"limit": "integer"})
    _add(dispatch, "ae.schedule.enable", _sched.schedule_enable, "schedule_mutate", "guarded", ["schedule_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.schedule.run_now", _sched.schedule_run_now, "schedule_mutate", "guarded", ["schedule_id"], {"reason": "string", "requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.schedule.disable", _sched.schedule_disable, "schedule_mutate", "guarded", ["schedule_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    # task (2 + 6 P1)
    _add(dispatch, "ae.task.get_request_context", _task.task_get_request_context, "task_read", "safe_read", ["task_id"], {})
    _add(dispatch, "ae.task.list_blocking_requests", _task.task_list_blocking_requests, "task_read", "safe_read", [], {"workflow": "string", "limit": "integer"})
    _add(dispatch, "ae.task.search_pending", _task.task_search_pending, "task_read", "safe_read", [], {"workflow": "string", "limit": "integer"})
    _add(dispatch, "ae.task.get_assignees", _task.task_get_assignees, "task_read", "safe_read", ["task_id"], {})
    _add(dispatch, "ae.task.get_overdue", _task.task_get_overdue, "task_read", "safe_read", [], {"limit": "integer"})
    _add(dispatch, "ae.task.cancel_admin", _task.task_cancel_admin, "task_read", "guarded", ["task_id", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.task.reassign", _task.task_reassign, "task_read", "guarded", ["task_id", "target_user_or_group", "reason"], {"requested_by": "string", "case_id": "string", "dry_run": "boolean"})
    _add(dispatch, "ae.task.explain_awaiting_input", _task.task_explain_awaiting_input, "task_read", "safe_read", ["request_id"], {})
    # credential (3 + 1 P1)
    _add(dispatch, "ae.credential_pool.get_availability", _cred.credential_pool_get_availability, "credential_read", "safe_read", ["pool_id"], {})
    _add(dispatch, "ae.credential_pool.get_waiting_requests", _cred.credential_pool_get_waiting_requests, "credential_read", "safe_read", ["pool_id"], {})
    _add(dispatch, "ae.credential_pool.diagnose_retry_state", _cred.credential_pool_diagnose_retry_state, "credential_read", "safe_read", [], {"request_id": "string", "pool_id": "string"})
    _add(dispatch, "ae.credential_pool.validate_for_workflow", _cred.credential_pool_validate_for_workflow, "credential_read", "safe_read", ["workflow_id"], {"pool_id": "string"})
    # dependency (3 P1)
    _add(dispatch, "ae.dependency.check_input_file_exists", _dep.dependency_check_input_file_exists, "dependency", "safe_read", ["workflow_id"], {"request_id": "string", "path_param": "string"})
    _add(dispatch, "ae.dependency.check_output_folder_writable", _dep.dependency_check_output_folder_writable, "dependency", "safe_read", ["workflow_id"], {"path_param": "string"})
    _add(dispatch, "ae.dependency.run_full_preflight_for_workflow", _dep.dependency_run_full_preflight_for_workflow, "dependency", "safe_read", ["workflow_id"], {"request_id": "string"})
    # misc (6)
    _add(dispatch, "ae.user.get_accessible_workflows", _misc.user_get_accessible_workflows, "user_read", "safe_read", ["user_id"], {})
    _add(dispatch, "ae.permission.get_workflow_permissions", _misc.permission_get_workflow_permissions, "permission_read", "safe_read", ["workflow_id"], {})
    _add(dispatch, "ae.permission.explain_user_access_issue", _misc.permission_explain_user_access_issue, "permission_read", "safe_read", ["user_id", "workflow_id"], {})
    _add(dispatch, "ae.platform.get_license_status", _misc.platform_get_license_status, "platform_read", "safe_read", [], {"tenant_id": "string"})
    _add(dispatch, "ae.platform.get_queue_depth", _misc.platform_get_queue_depth, "platform_read", "safe_read", [], {"tenant_id": "string"})
    _add(dispatch, "ae.result.get_failure_category", _misc.result_get_failure_category, "result_read", "safe_read", ["request_id"], {})
    # support_composite (8)
    _add(dispatch, "ae.support.diagnose_failed_request", _support.diagnose_failed_request, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.diagnose_stuck_running_request", _support.diagnose_stuck_running_request, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.diagnose_retry_due_to_credentials", _support.diagnose_retry_due_to_credentials, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.diagnose_no_output_file", _support.diagnose_no_output_file, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.diagnose_schedule_not_triggered", _support.diagnose_schedule_not_triggered, "support_composite", "safe_read", ["schedule_id"], {})
    _add(dispatch, "ae.support.diagnose_user_cannot_find_workflow", _support.diagnose_user_cannot_find_workflow, "support_composite", "safe_read", ["user_id", "workflow_id"], {})
    _add(dispatch, "ae.support.diagnose_awaiting_input", _support.diagnose_awaiting_input, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.diagnose_agent_unavailable", _support.diagnose_agent_unavailable, "support_composite", "safe_read", ["agent_id"], {})
    _add(dispatch, "ae.support.diagnose_rdp_blocked_workflow", _support.diagnose_rdp_blocked_workflow, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.build_case_snapshot", _support.build_case_snapshot, "support_composite", "safe_read", ["request_id"], {})
    _add(dispatch, "ae.support.prepare_human_handoff_note", _support.prepare_human_handoff_note, "support_composite", "safe_read", ["request_id"], {})

    descriptions = _DESCRIPTIONS
    eager_count = 0
    for tool_name, (async_fn, cat, safety, required, optional) in dispatch.items():
        app_cat = _CATEGORY_MAP.get(cat, "status")
        tier = _SAFETY_TO_TIER.get(safety, "read_only")
        params = _build_params(required, optional)
        desc = descriptions.get(tool_name, f"AE MCP tool: {tool_name}")
        always_available = tool_name in _ALWAYS_AVAILABLE_MCP_TOOLS
        definition = ToolDefinition(
            name=tool_name,
            description=desc,
            category=app_cat,
            tier=tier,
            parameters=params,
            required_params=required,
            always_available=always_available,
            metadata={
                "source": "mcp",
                "mcp_category": cat,
                "safety": safety,
                "hydration_mode": "eager" if always_available else "lazy",
            },
        )
        if always_available:
            eager_count += 1

        def _make_handler_factory(_name, _fn):
            def _factory():
                def _handler(**kwargs):
                    return _run_mcp_tool(_name, _fn, **kwargs)
                return _handler
            return _factory

        tool_registry.register_catalog_entry(
            ToolCatalogEntry.from_definition(
                definition,
                source_ref=tool_name,
                hydration_mode="eager" if always_available else "lazy",
                latency_class="medium",
                mutating=tier != "read_only",
            ),
            handler_factory=_make_handler_factory(tool_name, async_fn),
            hydrate=always_available,
        )

    logger.info(
        "Cataloged %d MCP tools with main app registry (%d eager, %d lazy)",
        len(dispatch),
        eager_count,
        len(dispatch) - eager_count,
    )


def _add(dispatch, name, fn, cat, safety, required, optional):
    dispatch[name] = (fn, cat, safety, required, optional)


def _build_params(required: list[str], optional: dict) -> dict:
    out = {}
    for r in required:
        out[r] = {"type": "string", "description": r.replace("_", " ").title()}
    for k, v in optional.items():
        t = "string"
        if v == "integer":
            t = "integer"
        elif v == "boolean":
            t = "boolean"
        out[k] = {"type": t, "description": k.replace("_", " ").title()}
    return out


_DESCRIPTIONS = {
    "ae.request.get_by_id": "Fetch full automation request record by ID.",
    "ae.request.get_status": "Fetch current request status with timestamps.",
    "ae.request.get_summary": "One-shot request support summary: workflow, user, agent, status, error, timing.",
    "ae.request.search": "Search requests by filters: workflow, user, status, agent, time range.",
    "ae.request.list_for_user": "Get all requests submitted by a specific user.",
    "ae.request.list_for_workflow": "Get requests for a specific workflow within a time range.",
    "ae.request.list_by_status": "Fetch requests in a specific status (e.g. Failure, Retry, Running).",
    "ae.request.list_stuck": "Detect stuck requests running longer than threshold minutes.",
    "ae.request.list_failed_recently": "List recently failed requests within a time window.",
    "ae.request.list_retrying": "List requests currently in Retry status.",
    "ae.request.list_awaiting_input": "List requests blocked waiting for human input/approval.",
    "ae.request.get_input_parameters": "Get runtime input parameters of a request (optional masking).",
    "ae.request.get_failure_message": "Fetch the latest failure/error message for a request.",
    "ae.request.build_support_snapshot": "Build a structured triage payload for a support case.",
    "ae.request.list_recent": "List recent requests for support review.",
    "ae.request.get_source_context": "Show trigger source: schedule, catalog, API.",
    "ae.request.get_time_details": "Timing breakdown: created, picked, completed, duration.",
    "ae.request.get_execution_details": "Fetch full execution metadata.",
    "ae.request.get_audit_logs": "Fetch the audit trail for a request.",
    "ae.request.get_step_logs": "Fetch step-level execution logs.",
    "ae.request.get_live_progress": "Get current running state and active step for a live request.",
    "ae.request.get_last_error_step": "Identify the step where failure occurred.",
    "ae.request.get_manual_intervention_context": "Get HITL block details: task ID, assignee, pending fields.",
    "ae.request.get_last_successful_step": "Last successful step for resume planning.",
    "ae.request.compare_attempts": "Compare multiple attempts for the same logical request.",
    "ae.request.export_diagnostic_bundle": "Export case evidence for escalation.",
    "ae.request.generate_support_narrative": "Plain-language support summary for handoff.",
    "ae.request.restart_failed": "[GUARDED] Restart a failed request with the same parameters.",
    "ae.request.terminate_running": "[PRIVILEGED] Terminate an actively running request.",
    "ae.request.resubmit_from_failure_point": "[GUARDED] Resume a request from its failure point.",
    "ae.request.add_support_comment": "Add a support action note/comment to a request.",
    "ae.request.cancel_new_or_retry": "[GUARDED] Cancel a request that has not started (New or Retry).",
    "ae.request.resubmit_from_start": "[GUARDED] Resubmit request from the beginning.",
    "ae.request.tag_case_reference": "Link request to a support case.",
    "ae.request.raise_manual_handoff": "Mark request for human handling.",
    "ae.workflow.search": "Search workflows by name, description, or category.",
    "ae.workflow.list_for_user": "Get workflows visible to a user.",
    "ae.workflow.get_details": "Get full workflow configuration details.",
    "ae.workflow.get_runtime_parameters": "Get the input parameter schema for a workflow.",
    "ae.workflow.get_flags": "Get monitoring, checkpoint, retry, and logging flags.",
    "ae.workflow.get_assignment_targets": "Get assigned agents and controllers for a workflow.",
    "ae.workflow.get_permissions": "Get permission configuration for a workflow.",
    "ae.workflow.get_by_id": "Fetch workflow by ID for support context.",
    "ae.workflow.get_recent_failure_stats": "Recent failure counts and last failure for a workflow.",
    "ae.workflow.enable": "[GUARDED] Re-enable a disabled workflow.",
    "ae.workflow.disable": "[GUARDED] Disable a workflow.",
    "ae.workflow.assign_to_agent": "[GUARDED] Assign a workflow to a specific agent.",
    "ae.workflow.update_permissions": "[PRIVILEGED] Update access control permissions for a workflow.",
    "ae.workflow.rollback_version": "[PRIVILEGED] Rollback workflow to a previous version.",
    "ae.agent.list_stopped": "List all agents in Stopped/Disconnected state.",
    "ae.agent.list_unknown": "List agents in unknown state.",
    "ae.agent.get_status": "Get current status and health of a specific agent.",
    "ae.agent.get_details": "Get full details of an agent.",
    "ae.agent.get_current_load": "Get active workload summary for an agent.",
    "ae.agent.get_running_requests": "Get requests currently executing on an agent.",
    "ae.agent.get_assigned_workflows": "Get workflows assigned to an agent.",
    "ae.agent.get_connectivity_state": "Check agent connectivity to controller and platform.",
    "ae.agent.get_rdp_session_state": "Check RDP/desktop session state for an agent.",
    "ae.agent.list_running": "List agents currently running and their load.",
    "ae.agent.get_recent_failures": "Recent request failures for an agent.",
    "ae.agent.get_last_heartbeat": "Last heartbeat and connectivity for an agent.",
    "ae.agent.collect_diagnostics": "Collect agent diagnostics for support.",
    "ae.agent.restart_service": "[PRIVILEGED] Restart the AE agent service.",
    "ae.agent.clear_stale_rdp_session": "[PRIVILEGED] Clear a stuck RDP session on an agent.",
    "ae.schedule.list_for_workflow": "Get schedules linked to a workflow.",
    "ae.schedule.get_details": "Get full schedule configuration.",
    "ae.schedule.get_missed_runs": "Detect missed scheduled runs.",
    "ae.schedule.get_recent_schedule_generated_requests": "Map schedule to requests it generated.",
    "ae.schedule.diagnose_not_triggered": "One-shot diagnosis of why a schedule didn't trigger.",
    "ae.schedule.get_next_runs": "Upcoming scheduled run times.",
    "ae.schedule.get_last_runs": "Last run times and outcomes for a schedule.",
    "ae.schedule.enable": "[GUARDED] Re-enable a disabled schedule.",
    "ae.schedule.run_now": "[GUARDED] Trigger a schedule run immediately.",
    "ae.schedule.disable": "[GUARDED] Disable a schedule.",
    "ae.task.get_request_context": "Map a task to its parent request and workflow.",
    "ae.task.list_blocking_requests": "List requests blocked by pending tasks.",
    "ae.task.search_pending": "Search pending tasks by workflow, assignee, or age.",
    "ae.task.get_assignees": "List users who can be assigned to a task.",
    "ae.task.get_overdue": "List overdue tasks for a workflow or assignee.",
    "ae.task.cancel_admin": "[GUARDED] Cancel a task (admin).",
    "ae.task.reassign": "[GUARDED] Reassign a task to another user.",
    "ae.task.explain_awaiting_input": "Explain why a request is awaiting input and what is needed.",
    "ae.credential_pool.get_availability": "Check available vs in-use credentials in a pool.",
    "ae.credential_pool.get_waiting_requests": "Get requests waiting on a credential pool.",
    "ae.credential_pool.diagnose_retry_state": "One-shot diagnosis of credential-related Retry state.",
    "ae.credential_pool.validate_for_workflow": "Validate credential pool configuration for a workflow.",
    "ae.dependency.check_input_file_exists": "Check if an input file/path exists for a workflow run.",
    "ae.dependency.check_output_folder_writable": "Check if output folder is writable for a workflow.",
    "ae.dependency.run_full_preflight_for_workflow": "Run full preflight checks for a workflow (files, creds, agent).",
    "ae.user.get_accessible_workflows": "Get workflows accessible to a user.",
    "ae.permission.get_workflow_permissions": "Get the permission map for a workflow.",
    "ae.permission.explain_user_access_issue": "One-shot diagnosis of why a user cannot see or run a workflow.",
    "ae.platform.get_license_status": "Get current platform license state.",
    "ae.platform.get_queue_depth": "Get queue depth summary across the platform.",
    "ae.result.get_failure_category": "Classify a request failure into a normalized category.",
    "ae.support.diagnose_failed_request": "One-shot diagnosis of a failed request.",
    "ae.support.diagnose_stuck_running_request": "Diagnose a request hung in Running state.",
    "ae.support.diagnose_retry_due_to_credentials": "Diagnose Retry state caused by credential pool.",
    "ae.support.diagnose_no_output_file": "Diagnose why a completed request produced no output file.",
    "ae.support.diagnose_schedule_not_triggered": "Diagnose why a scheduled job never started.",
    "ae.support.diagnose_user_cannot_find_workflow": "Diagnose why a user cannot see or access a workflow.",
    "ae.support.diagnose_awaiting_input": "Diagnose why a request is in Awaiting Input state.",
    "ae.support.diagnose_agent_unavailable": "Diagnose why an agent is unavailable.",
    "ae.support.diagnose_rdp_blocked_workflow": "Diagnose workflow blocked by RDP/session state on agent.",
    "ae.support.build_case_snapshot": "Build a full case snapshot for escalation.",
    "ae.support.prepare_human_handoff_note": "Generate human-readable handoff note for support.",
}

# Register on import when enabled
_register_mcp_tools()
