"""
Status & health monitoring tools.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo
import re
from config.client_policy import format_client_message
from config.settings import CONFIG
from security.workflow_access import (
    can_execute_workflow,
    can_view_workflow,
    default_org_code,
    get_user_accessible_workflow_names,
    is_execute_enforced,
    is_read_enforced,
)
from tools.base import AutomationEdgeClient, ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.status")


def _get_display_timezone() -> tuple[timezone | ZoneInfo, str]:
    tz_name = str(CONFIG.get("DISPLAY_TIMEZONE") or "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=5, minutes=30))
    label = "IST" if tz_name in {"Asia/Kolkata", "Asia/Calcutta"} else tz_name
    return tz, label


def _format_display_time(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return "recently"
    tz, label = _get_display_timezone()
    localized = value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=timezone.utc).astimezone(tz)
    return localized.strftime("%Y-%m-%d %I:%M:%S %p") + f" {label}"


def _extract_execution_ref(record: dict | None) -> str:
    if not isinstance(record, dict):
        return ""
    return str(
        record.get("id")
        or record.get("automationRequestId")
        or record.get("requestId")
        or record.get("executionId")
        or ""
    ).strip()


def _extract_workflow_ref(record: dict | None) -> str:
    if not isinstance(record, dict):
        return ""
    workflow_meta = record.get("workflowConfiguration") or record.get("workflow") or {}
    return str(
        record.get("workflowName")
        or record.get("workflow_name")
        or workflow_meta.get("name")
        or ""
    ).strip()


def _get_new_status_diagnosis(client, record: dict | None, execution_id: str = "") -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    status = str(record.get("status") or record.get("state") or "").strip().upper()
    if status not in {"NEW", "QUEUED", "PENDING"}:
        return {}
    diagnoser = getattr(client, "diagnose_new_execution", None)
    if not callable(diagnoser):
        return {}
    try:
        diagnosis = diagnoser(record, execution_id=execution_id)
        return diagnosis if isinstance(diagnosis, dict) else {}
    except Exception as exc:
        logger.warning(
            "Could not diagnose pending NEW execution %s: %s",
            execution_id or _extract_execution_ref(record) or "unknown",
            exc,
        )
        return {}


def _maybe_refresh_execution_payload(client, execution_id: str, record: dict | None) -> dict:
    refresher = getattr(type(client), "refresh_execution_payload", None)
    if not callable(refresher):
        return dict(record or {}) if isinstance(record, dict) else {}
    try:
        refreshed = refresher(
            client,
            execution_id,
            record=record,
            workflow_name=_extract_workflow_ref(record),
        )
        return refreshed if isinstance(refreshed, dict) else (dict(record or {}) if isinstance(record, dict) else {})
    except Exception as exc:
        logger.debug("Could not refresh execution payload for %s: %s", execution_id, exc)
        return dict(record or {}) if isinstance(record, dict) else {}


def _extract_workflow_response_message(record: dict | None) -> str:
    try:
        return str(AutomationEdgeClient.extract_workflow_response_message(record) or "").strip()
    except Exception:
        return ""


def _workflow_access_denied_message(
    workflow_name: str = "",
    user_id: str = "",
    org_code: str = "",
    *,
    action: str = "view",
) -> str:
    """Build a user-friendly denial message with a list of accessible workflows."""
    friendly_name = workflow_name or "this workflow"
    require_exec = action in ("trigger", "execute", "run")
    available = []
    if user_id:
        try:
            available = get_user_accessible_workflow_names(
                user_id, org_code, require_execute=require_exec, limit=15,
            )
        except Exception:
            pass

    if available:
        wf_list = "\n".join(f"  \u2022 `{n}`" for n in available)
        return (
            f"I’m unable to {action} **{friendly_name}** with the workflow access currently assigned to your account.\n\n"
            f"The workflows currently available to your account are:\n{wf_list}\n\n"
            "Please choose one from this list, or ask an AutomationEdge administrator to review your workflow access."
        )
    return (
        f"I’m unable to {action} **{friendly_name}** because no eligible workflow access is currently available for your account.\n\n"
        "Please contact an AutomationEdge administrator to review and update your workflow permissions."
    )


def _is_visible_workflow_record(client, record: dict, user_id: str, org_code: str) -> bool:
    if not user_id:
        return False
    wf_id = (
        record.get("workflowId")
        or record.get("workflow_id")
        or (record.get("workflowConfiguration") or {}).get("id")
    )
    wf_name = (
        record.get("workflowName")
        or record.get("workflow_name")
        or (record.get("workflowConfiguration") or {}).get("name")
    )
    resolved_org = str(
        record.get("orgCode")
        or record.get("org_code")
        or org_code
        or default_org_code()
    ).strip()
    if not wf_id and wf_name:
        getter = getattr(client, "get_cached_workflow_id", None)
        if callable(getter):
            try:
                wf_id = getter(str(wf_name), user_id=user_id, org_code=resolved_org)
            except TypeError:
                wf_id = getter(str(wf_name))
    return bool(wf_id and can_view_workflow(user_id, str(wf_id), resolved_org))


def _resolve_cached_workflow_name_for_user(client, workflow_name: str, user_id: str = "", org_code: str = "") -> str:
    if is_read_enforced() and not str(user_id or "").strip():
        return ""
    resolver = getattr(client, "resolve_cached_workflow_name", None)
    if not callable(resolver):
        return str(workflow_name or "").strip()
    try:
        return resolver(workflow_name, user_id=user_id, org_code=org_code)
    except TypeError:
        return resolver(workflow_name)


def _resolve_workflow_via_rag_for_user(client, query: str, user_id: str = "", org_code: str = "") -> str:
    if is_read_enforced() and not str(user_id or "").strip():
        return ""
    resolver = getattr(client, "resolve_workflow_via_rag", None)
    if not callable(resolver):
        return ""
    try:
        return resolver(query, user_id=user_id, org_code=org_code)
    except TypeError:
        return resolver(query)


def _get_assigned_agents_for_workflow(client, workflow_name: str) -> list[dict]:
    getter = getattr(client, "get_workflow_agents", None)
    if not callable(getter):
        return []

    try:
        entries = getter() or []
    except Exception as exc:
        logger.warning("Could not load assigned agents for %s: %s", workflow_name, exc)
        return []

    if not isinstance(entries, list):
        return []

    normalized = str(workflow_name or "").strip().lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        workflow_meta = entry.get("workflow") or entry.get("workflowConfiguration") or {}
        candidate = str(
            workflow_meta.get("name")
            or entry.get("workflowName")
            or ""
        ).strip().lower()
        if candidate != normalized:
            continue
        agents = entry.get("agents") or []
        return [agent for agent in agents if isinstance(agent, dict)]
    return []


def _assigned_agent_summary(agents: list[dict]) -> str:
    labels: list[str] = []
    for agent in agents:
        name = str(
            agent.get("agentName")
            or agent.get("name")
            or agent.get("agentId")
            or "Unknown"
        ).strip()
        state = str(agent.get("agentState") or agent.get("state") or "UNKNOWN").strip().upper()
        labels.append(f"{name} ({state})")
    return ", ".join(labels)


def _primary_assigned_agent_name(agents: list[dict]) -> str:
    if len(agents) != 1:
        return ""
    agent = agents[0]
    return str(
        agent.get("agentName")
        or agent.get("name")
        or agent.get("agentId")
        or ""
    ).strip()


def _get_workflow_instances_compat(client, workflow_name: str, limit: int, status_filter: str | None = None):
    getter = getattr(client, "get_workflow_instances")
    try:
        if status_filter:
            return getter(workflow_name, limit=limit, status_filter=status_filter)
        return getter(workflow_name, limit=limit)
    except TypeError:
        return getter(workflow_name, limit)


def _extract_related_issue_text(log_result: dict[str, Any]) -> str:
    if not isinstance(log_result, dict):
        return ""

    parts: list[str] = []
    for key in ("report", "error", "message", "note", "workflow_name"):
        value = log_result.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    primary_error = log_result.get("primary_error")
    if isinstance(primary_error, dict):
        for key in ("error_message", "error_label", "component"):
            value = primary_error.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())

    for group in log_result.get("error_groups") or []:
        if not isinstance(group, dict):
            continue
        value = group.get("error_message")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    for block in log_result.get("error_blocks") or []:
        if not isinstance(block, dict):
            continue
        value = block.get("error_message")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        for line in block.get("lines") or []:
            if isinstance(line, str) and line.strip():
                parts.append(line.strip())

    for line in log_result.get("logs") or []:
        if isinstance(line, str) and line.strip():
            parts.append(line.strip())

    return "\n".join(parts)


def _detect_related_issue(log_result: dict[str, Any]) -> dict[str, str] | None:
    text = _extract_related_issue_text(log_result)
    lowered = text.lower()
    if not lowered:
        return None

    life_asia_markers = ("life asia", "life_asia", "lifeasia")
    tebt_markers = ("tebt",)
    life_asia_context = ("connect", "connection", "timeout", "down", "unavailable", "socket", "host", "service")
    tebt_context = ("login", "portal", "credential", "password", "auth", "authentication", "session", "sign in", "signin")

    if any(marker in lowered for marker in life_asia_markers) and (
        any(marker in lowered for marker in life_asia_context) or "life asia" in lowered
    ):
        workflow_name = str(CONFIG.get("LIFE_ASIA_HEALTH_CHECK_WORKFLOW", "") or "").strip()
        if workflow_name:
            return {
                "issue_type": "life_asia",
                "issue_label": "Life Asia",
                "health_check_label": "Life Asia health check",
                "workflow_name": workflow_name,
            }

    if any(marker in lowered for marker in tebt_markers) and (
        any(marker in lowered for marker in tebt_context) or "tebt" in lowered
    ):
        workflow_name = str(CONFIG.get("TEBT_HEALTH_CHECK_WORKFLOW", "") or "").strip()
        if workflow_name:
            return {
                "issue_type": "tebt",
                "issue_label": "TEBT",
                "health_check_label": "TEBT health check",
                "workflow_name": workflow_name,
            }

    return None


def _normalize_related_issue_sentence(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    text = text.strip(" .,:;")
    if not text:
        return ""
    if len(text) > 220:
        text = text[:217].rsplit(" ", 1)[0].rstrip(" ,;:")
        if not text:
            return ""
        text += "..."
    if text[-1] not in ".!?":
        text += "."
    return text


def _extract_related_issue_reason(log_result: dict[str, Any], issue_info: dict[str, str]) -> str:
    if not isinstance(log_result, dict):
        return ""

    issue_label = str(issue_info.get("issue_label") or "Related application").strip()
    issue_type = str(issue_info.get("issue_type") or "").strip().lower()
    context_tokens = {
        "connect",
        "connection",
        "timeout",
        "down",
        "unavailable",
        "socket",
        "host",
        "service",
        "login",
        "portal",
        "credential",
        "password",
        "auth",
        "authentication",
        "session",
        "sign in",
        "signin",
    }
    markers = {issue_label.lower()}
    if issue_type == "life_asia":
        markers.update({"life asia", "life_asia", "lifeasia"})
    elif issue_type == "tebt":
        markers.add("tebt")

    candidates: list[str] = []
    primary_error = log_result.get("primary_error")
    if isinstance(primary_error, dict):
        for key in ("error_message", "error_label", "component"):
            value = primary_error.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    for group in log_result.get("error_groups") or []:
        if isinstance(group, dict):
            value = group.get("error_message")
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    for block in log_result.get("error_blocks") or []:
        if not isinstance(block, dict):
            continue
        value = block.get("error_message")
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        for line in block.get("lines") or []:
            if isinstance(line, str) and line.strip():
                candidates.append(line.strip())

    for key in ("report", "error", "message", "note"):
        value = log_result.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    seen: set[str] = set()
    cleaned_candidates: list[str] = []
    for candidate in candidates:
        normalized = _normalize_related_issue_sentence(candidate)
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned_candidates.append(normalized)

    for candidate in cleaned_candidates:
        lowered = candidate.lower()
        if any(marker in lowered for marker in markers):
            return candidate

    for candidate in cleaned_candidates:
        lowered = candidate.lower()
        if any(token in lowered for token in context_tokens):
            if issue_label and issue_label.lower() not in lowered:
                return _normalize_related_issue_sentence(f"{issue_label} issue: {candidate}")
            return candidate

    if issue_label:
        return _normalize_related_issue_sentence(f"{issue_label} related issue.")
    return cleaned_candidates[0] if cleaned_candidates else ""


def _classify_related_health_status(status: str, *, has_request_id: bool = True) -> str:
    normalized = str(status or "").strip().upper()
    success_statuses = {"COMPLETE", "COMPLETED", "SUCCESS", "SUCCEEDED"}
    failure_statuses = {"FAILURE", "FAILED", "DOWN", "TERMINATED", "CANCELLED"}
    if normalized in success_statuses:
        return "up"
    if normalized in failure_statuses:
        return "down"
    if normalized == "ERROR" and not has_request_id:
        return "unknown"
    if normalized:
        return "pending"
    return "unknown"


def _build_related_issue_status_message(
    display_name: str,
    latest_ts: datetime | None,
    related_issue_check: dict[str, Any],
    *,
    org_code: str = "",
) -> str:
    issue_label = str(related_issue_check.get("issue_label") or "Related application").strip()
    failure_reason = _normalize_related_issue_sentence(related_issue_check.get("failure_reason") or "")
    health_status = str(related_issue_check.get("status") or "").strip()
    health_state = str(related_issue_check.get("application_status") or "").strip().lower()
    health_check_run = bool(related_issue_check.get("health_check_run"))
    if not health_check_run and health_state in {"", "unknown"}:
        health_state = "not_checked"
    elif not health_state:
        health_state = _classify_related_health_status(
            health_status,
            has_request_id=bool(str(related_issue_check.get("request_id") or "").strip()),
        )

    if latest_ts:
        intro = f"Status: Failed. The latest run for **{display_name}** failed on {_format_display_time(latest_ts)}."
    else:
        intro = f"Status: Failed. The latest run for **{display_name}** failed recently."

    parts = [intro]
    if failure_reason:
        parts.append(f"Likely cause: {failure_reason}")
    else:
        parts.append(f"Likely cause: {issue_label} related issue.")

    if health_state == "not_checked":
        parts.append(
            format_client_message(
                "related_issue_status_retry_guard",
                "Current {application} health has not been checked yet. If you want to retry this workflow, I will first verify the current {application} health and only then proceed.",
                org_code=org_code,
                application=issue_label,
                workflow_name=display_name,
                status=health_status,
            )
        )
    elif health_state == "up":
        parts.append(
            format_client_message(
                "related_issue_status_up",
                "{application} is currently up and running. This execution is still marked as failed, so the issue appears temporary or specific to that run.",
                org_code=org_code,
                application=issue_label,
                workflow_name=display_name,
                status=health_status,
            )
        )
    elif health_state == "down":
        parts.append(
            format_client_message(
                "related_issue_status_down",
                "{application} is currently unavailable, so this execution failed because the dependent application is down.",
                org_code=org_code,
                application=issue_label,
                workflow_name=display_name,
                status=health_status,
            )
        )
    elif health_state == "pending":
        parts.append(
            format_client_message(
                "related_issue_status_running",
                "{application} health check is still running. Current status: {status}.",
                org_code=org_code,
                application=issue_label,
                workflow_name=display_name,
                status=health_status or "UNKNOWN",
            )
        )
    else:
        parts.append(
            format_client_message(
                "related_issue_status_unknown",
                "I could not confirm the current {application} status right now.",
                org_code=org_code,
                application=issue_label,
                workflow_name=display_name,
                status=health_status,
            )
        )

    return " ".join(part.strip() for part in parts if str(part or "").strip())


def _build_related_health_check_summary(issue_info: dict[str, str], status: str, org_code: str = "") -> str:
    issue_type = str(issue_info.get("issue_type") or "").strip().lower()
    normalized = str(status or "").strip().upper()
    success_statuses = {"COMPLETE", "COMPLETED", "SUCCESS", "SUCCEEDED"}
    failure_statuses = {"FAILURE", "FAILED", "ERROR", "DOWN", "TERMINATED", "CANCELLED"}

    if issue_type == "life_asia":
        if normalized in success_statuses:
            return format_client_message(
                "life_asia_health_up",
                "Life Asia is currently up and running.",
                org_code=org_code,
                application="Life Asia",
                status=status,
            )
        if normalized in failure_statuses:
            return format_client_message(
                "life_asia_health_down",
                "Life Asia is currently unavailable.",
                org_code=org_code,
                application="Life Asia",
                status=status,
            )
        return format_client_message(
            "life_asia_health_running",
            "Life Asia health check is still running. Current status: {status}.",
            org_code=org_code,
            application="Life Asia",
            status=status,
        )

    if issue_type == "tebt":
        if normalized in success_statuses:
            return format_client_message(
                "tebt_health_up",
                "TEBT is currently up and running.",
                org_code=org_code,
                application="TEBT",
                status=status,
            )
        if normalized in failure_statuses:
            return format_client_message(
                "tebt_health_down",
                "TEBT is currently unavailable.",
                org_code=org_code,
                application="TEBT",
                status=status,
            )
        return format_client_message(
            "tebt_health_running",
            "TEBT health check is still running. Current status: {status}.",
            org_code=org_code,
            application="TEBT",
            status=status,
        )

    return f"Related health check status: {status}."


def _run_related_health_check(
    client,
    issue_info: dict[str, str],
    org_code: str = "",
) -> dict[str, Any]:
    workflow_name = str(issue_info.get("workflow_name") or "").strip()
    if not workflow_name:
        return {}

    resolved_org = str(org_code or default_org_code()).strip()
    workflow_id = ""
    getter = getattr(client, "get_cached_workflow_id", None)
    if callable(getter):
        try:
            workflow_id = getter(workflow_name, user_id="", org_code=resolved_org)
        except TypeError:
            workflow_id = getter(workflow_name)
        except Exception as exc:
            logger.warning("Health check workflow id lookup failed for %s: %s", workflow_name, exc)

    # Pick a running agent so AE dispatches to the right one
    _picked_agent_id = ""
    _picked_agent_name = ""
    try:
        wf_assigned = _get_assigned_agents_for_workflow(client, workflow_name)
        for a in (wf_assigned or []):
            if str(a.get("agentState") or "").upper() in {"RUNNING", "CONNECTED", "ACTIVE"}:
                _picked_agent_id = str(a.get("id") or a.get("agentId") or a.get("uuid") or "")
                _picked_agent_name = str(a.get("agentName") or a.get("name") or "")
                break
    except Exception:
        pass

    raw = client.execute_workflow(
        workflow_name=workflow_name,
        workflow_id=workflow_id or workflow_name,
        params={},
        org_code=resolved_org,
        user_id="",
        source="ops-agent-related-health-check",
        mail_subject="null",
        agent_id=_picked_agent_id,
        agent_name=_picked_agent_name,
    )
    request_id = (
        raw.get("automationRequestId")
        or raw.get("requestId")
        or raw.get("id")
        or ""
    )
    final_status = str(raw.get("status") or raw.get("state") or "QUEUED")
    poll_raw: dict[str, Any] = {}
    if request_id:
        try:
            poll_result = client.poll_execution_status(
                execution_id=str(request_id),
                poll_interval_sec=2,
                max_attempts=10,
            )
            if isinstance(poll_result, dict):
                final_status = str(poll_result.get("status") or final_status)
                poll_raw = poll_result.get("raw") or {}
        except Exception as exc:
            logger.warning("Health check polling failed for %s (%s): %s", workflow_name, request_id, exc)

    return {
        "issue_type": issue_info.get("issue_type", ""),
        "issue_label": issue_info.get("issue_label", ""),
        "health_check_label": issue_info.get("health_check_label", ""),
        "workflow_name": workflow_name,
        "request_id": request_id,
        "status": final_status,
        "used_admin_scope": bool(CONFIG.get("RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE", True)),
        "message": _build_related_health_check_summary(issue_info, final_status, org_code=resolved_org),
        "raw": poll_raw or raw,
    }


def _maybe_add_related_issue_health_check(
    latest: dict[str, Any],
    org_code: str = "",
) -> dict[str, Any] | None:
    """Diagnose Life Asia / TEBT related failures without running a health check.

    Status checks should explain the likely application-related reason, but the
    actual application health validation is deferred until the user asks to
    retry or retrigger the failed workflow.
    """
    if not bool(CONFIG.get("ENABLE_RELATED_ISSUE_HEALTH_CHECK", False)):
        return None

    latest_status = str(latest.get("status") or "").strip().upper()
    if latest_status not in {"FAILURE", "FAILED", "ERROR"}:
        return None

    execution_id = latest.get("id") or latest.get("automationRequestId")
    if not execution_id:
        return None

    try:
        from tools.log_tools import get_execution_logs

        log_result = get_execution_logs(str(execution_id), tail=0)
    except Exception as exc:
        logger.warning("Related issue detection skipped for execution_id=%s: %s", execution_id, exc)
        return None

    issue_info = _detect_related_issue(log_result)
    if not issue_info:
        return None
    failure_reason = _extract_related_issue_reason(log_result, issue_info)
    issue_label = str(issue_info.get("issue_label") or "Related application").strip()
    guidance = format_client_message(
        "related_issue_status_retry_guard",
        "Current {application} health has not been checked yet. If you want to retry this workflow, I will first verify the current {application} health and only then proceed.",
        org_code=org_code,
        application=issue_label,
        workflow_name=str(latest.get("workflowName") or latest.get("workflow_name") or "").strip(),
        status="NOT_CHECKED",
    )
    return {
        "issue_type": issue_info.get("issue_type", ""),
        "issue_label": issue_label,
        "health_check_label": issue_info.get("health_check_label", ""),
        "workflow_name": issue_info.get("workflow_name", ""),
        "request_id": "",
        "status": "NOT_CHECKED",
        "used_admin_scope": False,
        "message": guidance,
        "failure_reason": failure_reason,
        "application_status": "not_checked",
        "health_check_run": False,
        "retry_health_check_required": True,
    }


def check_workflow_status(
    workflow_name: str = "",
    status: str = "",
    user_id: str = "",
    org_code: str = "",
) -> dict:
    """Check the status of bots/workflows, including a 24h summary and filtering.
    
    If workflow_name is provided, checks that specific bot using semantic resolution if needed.
    If workflow_name is empty, provides a global summary for all bots.
    """
    client = get_ae_client()
    status_filter = str(status or "").strip()
    query_name = str(workflow_name or "").strip()
    
    # 1. Detect if query_name is a numeric Request ID
    if query_name.isdigit() and len(query_name) > 4:  # Likely a request ID, not a short workflow ID
        try:
            instance = client.get_workflow_instance_by_id(query_name)
            if instance and (
                not user_id
                or not is_read_enforced()
                or _is_visible_workflow_record(client, instance, user_id, org_code)
            ):
                return _format_single_instance_response(instance, client=client, org_code=org_code)
            if instance and (user_id or is_read_enforced()):
                return {
                    "workflow_name": query_name,
                    "status": "UNAUTHORIZED",
                    "message": _workflow_access_denied_message(
                        query_name, user_id=user_id, org_code=org_code, action="view",
                    ),
                }
        except Exception as exc:
            logger.warning(f"Direct request ID lookup failed for {query_name}: {exc}")

    # 2. Resolve technical name
    resolved_name = ""
    if query_name:
        # Try local cache first (exact/WF_ prefix variants)
        resolved_name = _resolve_cached_workflow_name_for_user(
            client,
            query_name,
            user_id=user_id,
            org_code=org_code,
        )
        
        # If not found, try RAG/Semantic resolution for fuzzy/partial names
        if not resolved_name:
            logger.info(f"Fuzzy match not found for '{query_name}', trying RAG resolution...")
            resolved_name = _resolve_workflow_via_rag_for_user(
                client,
                query_name,
                user_id=user_id,
                org_code=org_code,
            )

        if (user_id or is_read_enforced()) and not resolved_name:
            return {
                "workflow_name": query_name,
                "status": "UNAUTHORIZED",
                "message": _workflow_access_denied_message(
                    query_name, user_id=user_id, org_code=org_code, action="view",
                ),
            }
            
    name_to_check = resolved_name or query_name
    
    try:
        # Increase limit to 300 to find older executions
        instances = _get_workflow_instances_compat(
            client,
            name_to_check,
            limit=300,
            status_filter=status_filter if status_filter else None,
        )
        if user_id or is_read_enforced():
            instances = [
                item for item in instances
                if isinstance(item, dict) and _is_visible_workflow_record(client, item, user_id, org_code)
            ]
    except Exception as exc:
        msg = f"Failed to fetch status for '{name_to_check or 'All Bots'}': {exc}"
        logger.warning(msg)
        return {
            "workflow_name": name_to_check or "All Bots",
            "status": "UNKNOWN",
            "error_message": str(exc),
            "message": msg
        }

    if not instances:
        msg = f"No recent executions found"
        if name_to_check:
            msg += f" for '{name_to_check}'"
        if status_filter:
            msg += f" with status '{status_filter}'"
        return {
            "workflow_name": name_to_check or "All Bots",
            "status": "NO_EXECUTIONS",
            "message": msg,
        }

    # Latest instance details - ALWAYS present regardless of 24h
    latest = instances[0]
    latest_execution_id = _extract_execution_ref(latest)
    if latest_execution_id:
        latest = _maybe_refresh_execution_payload(client, latest_execution_id, latest)
    latest_ts = _parse_timestamp(latest.get("createdDate") or latest.get("started_at"))
    latest_status = latest.get("status")
    normalized_latest_status = str(latest_status or "").strip().upper()
    latest_detail_msg = _extract_workflow_response_message(latest)
    latest_error_message = ""
    if normalized_latest_status in {"FAILURE", "FAILED", "ERROR"}:
        latest_error_message = str(
            latest.get("errorMessage")
            or latest.get("errorDetails")
            or latest_detail_msg
            or ""
        ).strip()
    
    # 24-hour summary logic
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    summary = {
        "New": 0, "InProgress": 0, "Complete": 0, "Failure": 0, 
        "ExecutionStarted": 0, "Retry": 0, "Expired": 0, "Diverted": 0, 
        "Terminated": 0, "Cancelled": 0, "Resubmitted": 0, "Awaitinginput": 0
    }
    total_24h = 0
    
    recent_list = []
    for item in instances:
        ts = _parse_timestamp(
            item.get("createdDate") or item.get("started_at")
        )
        item_status = item.get("status", "Unknown")
        # Keep history of recent ones as requested by the user
        if len(recent_list) < 50:
            recent_list.append({
                "id": item.get("id") or item.get("automationRequestId"),
                "bot_name": item.get("workflowName") or (item.get("workflowConfiguration") or {}).get("name") or "Unknown Bot",
                "status": item_status,
                "time": str(ts) if ts else "Unknown",
                "agent": item.get("agentName")
            })

        if ts and ts >= cutoff:
            total_24h += 1
            if item_status in summary:
                summary[item_status] += 1
            else:
                summary[item_status] = summary.get(item_status, 0) + 1

    active_summary = {k: v for k, v in summary.items() if v > 0}

    # Resolve display name
    display_name = name_to_check if name_to_check else "All Bots"
    if latest:
        display_name = (
            latest.get("workflowName") or 
            (latest.get("workflowConfiguration") or {}).get("name") or 
            display_name
        )

    # User message construction
    latest_diag = _get_new_status_diagnosis(
        client,
        latest,
        execution_id=_extract_execution_ref(latest),
    ) if query_name else {}
    if query_name:
        time_str = f"on {_format_display_time(latest_ts)}" if latest_ts else "recently"
        msg = f"The absolute latest execution for bot '**{display_name}**' was {time_str} and its status is '**{latest_status}**'."
        if latest_ts and latest_ts < cutoff:
            msg += f" (Note: This run is older than 24 hours)."
        if normalized_latest_status in {"FAILURE", "FAILED", "ERROR"}:
            if latest_error_message:
                msg += f" Error: {latest_error_message}"
            else:
                msg += f" You can use execution ID `{latest.get('id') or latest.get('automationRequestId')}` to fetch logs if needed."
        elif normalized_latest_status in {"COMPLETE", "COMPLETED"}:
            if latest_detail_msg:
                msg += f" Result: {latest_detail_msg}"
            else:
                msg += f" You can use execution ID `{latest.get('id') or latest.get('automationRequestId')}` to fetch logs if needed."
        elif normalized_latest_status in {"NEW", "QUEUED", "PENDING"}:
            msg += (
                " The request is still waiting. I will first verify whether the assigned agent is running and, "
                "if it is running, whether another process is already in progress."
            )
        if latest_diag.get("summary"):
            msg = str(latest_diag.get("summary"))
    else:
        msg = f"Global status summary for all bots (Last 24 hours)."

    resolved_org = str(org_code or default_org_code()).strip()
    related_issue_check = _maybe_add_related_issue_health_check(latest, org_code=resolved_org) if query_name else None
    if related_issue_check and related_issue_check.get("message"):
        msg = _build_related_issue_status_message(
            display_name,
            latest_ts,
            related_issue_check,
            org_code=resolved_org,
        )

    result = {
        "bot_name": display_name,
        "workflow_name": display_name,
        "is_global_check": not bool(query_name),
        "latest_status": latest_status,
        "latest_execution": {
            "id": latest.get("id") or latest.get("automationRequestId"),
            "bot_name": latest.get("workflowName") or (latest.get("workflowConfiguration") or {}).get("name") or "Unknown Bot",
            "status": latest_status,
            "timestamp": str(latest_ts) if latest_ts else "Unknown"
        },
        "latest_execution_id": latest.get("id") or latest.get("automationRequestId"),
        "last_24h_summary": active_summary,
        "total_executions_24h": total_24h,
        "recent_executions": recent_list,
        "status_filter_applied": status_filter or "None",
        "message": msg
    }
    if latest_diag:
        result["diagnosis"] = latest_diag.get("reason")
        result["assigned_agents"] = latest_diag.get("assigned_agents") or []
        result["other_execution_id"] = latest_diag.get("other_execution_id") or ""
        result["other_workflow_name"] = latest_diag.get("other_workflow_name") or ""
    if latest_detail_msg:
        result["workflow_response_message"] = latest_detail_msg
    if latest_error_message:
        result["error_message"] = latest_error_message
    if related_issue_check:
        result["related_issue_check"] = related_issue_check
        if related_issue_check.get("failure_reason"):
            result["failure_reason"] = related_issue_check.get("failure_reason")
    return result


def _format_single_instance_response(instance: dict, client=None, org_code: str = "") -> dict:
    """Helper to format a single T4 instance into the standard status response."""
    client = client or get_ae_client()
    request_id = _extract_execution_ref(instance)
    instance = _maybe_refresh_execution_payload(client, request_id, instance) if request_id else dict(instance or {})
    ts = _parse_timestamp(instance.get("createdDate") or instance.get("started_at"))
    status = instance.get("status", "Unknown")
    bot_name = (
        instance.get("workflowName") or 
        (instance.get("workflowConfiguration") or {}).get("name") or 
        "Unknown Bot"
    )
    detail_msg = _extract_workflow_response_message(instance)
    
    time_str = f"on {_format_display_time(ts)}" if ts else "recently"
    # Ensure request_id is not empty
    rid_str = f"`{request_id}`" if request_id else "unknown"
    msg = f"Execution ID {rid_str} for bot '**{bot_name}**' was found. Its current status is '**{status}**' ({time_str})."
    diagnosis = _get_new_status_diagnosis(client, instance, execution_id=request_id)
    if diagnosis.get("summary"):
        msg = str(diagnosis.get("summary"))
    elif detail_msg and str(status or "").strip().upper() in {"COMPLETE", "FAILURE", "ERROR"}:
        msg = detail_msg

    resolved_org = str(org_code or default_org_code()).strip()
    related_issue_check = _maybe_add_related_issue_health_check(instance, org_code=resolved_org)
    if related_issue_check and related_issue_check.get("message"):
        msg = _build_related_issue_status_message(
            bot_name,
            ts,
            related_issue_check,
            org_code=resolved_org,
        )

    result = {
        "bot_name": bot_name,
        "workflow_name": bot_name,
        "status": status,
        "latest_status": status,
        "latest_execution": {
            "id": request_id,
            "bot_name": bot_name,
            "status": status,
            "timestamp": str(ts) if ts else "Unknown"
        },
        "latest_execution_id": request_id,
        "message": msg,
        "is_single_search": True
    }
    if detail_msg:
        result["workflow_response_message"] = detail_msg
    if diagnosis:
        result["diagnosis"] = diagnosis.get("reason")
        result["assigned_agents"] = diagnosis.get("assigned_agents") or []
        result["other_execution_id"] = diagnosis.get("other_execution_id") or ""
        result["other_workflow_name"] = diagnosis.get("other_workflow_name") or ""
    if related_issue_check:
        result["related_issue_check"] = related_issue_check
        if related_issue_check.get("failure_reason"):
            result["failure_reason"] = related_issue_check.get("failure_reason")
    return result


def _parse_timestamp(value):
    if value is None:
        return None
    try:
        val = float(value)
        # Handle milliseconds (13 digits) vs seconds
        if val > 10000000000:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass

    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def list_recent_failures(
    hours: int = 24,
    limit: int = 300,  # Increased default for deep search
    workflow_name: str = "",
    user_id: str = "",
    org_code: str = "",
) -> dict:
    client = get_ae_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))
    query_name = str(workflow_name or "").strip()

    # 1. Resolve technical name if query_name provided
    resolved_name = ""
    if query_name:
        resolved_name = _resolve_cached_workflow_name_for_user(
            client,
            query_name,
            user_id=user_id,
            org_code=org_code,
        )
        if not resolved_name:
            logger.info(f"Fuzzy match not found for '{query_name}' in failures check, trying RAG...")
            resolved_name = _resolve_workflow_via_rag_for_user(
                client,
                query_name,
                user_id=user_id,
                org_code=org_code,
            )
        if (user_id or is_read_enforced()) and not resolved_name:
            return {
                "failures": [],
                "total_count": 0,
                "time_window_hours": hours,
                "warning": _workflow_access_denied_message(query_name),
            }
            
    name_to_check = resolved_name or query_name
    data = []
    last_error = ""
    used_recent_endpoint = False
    skip_cutoff_filter = False

    if name_to_check:
        try:
            # Benefiting from the new paging logic in AutomationEdgeClient
            data = _get_workflow_instances_compat(
                client,
                name_to_check,
                limit=max(limit, 20),
                status_filter="Failure",
            )
            skip_cutoff_filter = True
            last_error = ""
        except Exception as exc:
            last_error = str(exc)

    if not data:
        # 2. Try global failures modern API if no specific name or previous search failed
        if not name_to_check:
            try:
                resp = client.request(
                    "GET",
                    "/api/v1/failures/recent",
                    use_rest_prefix=False,
                    silent_on_status=[400, 403, 404],
                )
                if isinstance(resp, dict):
                    data = resp.get("failures") or resp.get("executions") or resp.get("data") or []
                elif isinstance(resp, list):
                    data = resp
                used_recent_endpoint = True
                last_error = ""
            except Exception as exc:
                last_error = str(exc)
        
        # 3. Use paging-aware global T4 check if still no data
        if not data:
            try:
                # get_workflow_instances("") handles global listing with paging
                data = _get_workflow_instances_compat(
                    client,
                    "",
                    limit=limit,
                    status_filter="Failure",
                )
                last_error = ""
            except Exception as exc:
                if not last_error:
                    last_error = str(exc)

    if not isinstance(data, list):
        data = []

    if user_id or is_read_enforced():
        data = [
            item for item in data
            if isinstance(item, dict) and _is_visible_workflow_record(client, item, user_id, org_code)
        ]

    failures = []
    for item in data:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).upper()
        if status not in {"FAILURE", "ERROR", "FAILED"}:
            continue

        ts = _parse_timestamp(
            item.get("completedDate")
            or item.get("lastUpdatedDate")
            or item.get("createdDate")
            or item.get("started_at")
            or item.get("completed_at")
        )
        if ts and ts < cutoff and not used_recent_endpoint and not skip_cutoff_filter:
            continue

        failures.append(
            {
                "execution_id": item.get("id") or item.get("automationRequestId") or item.get("execution_id"),
                "workflow_name": item.get("workflowName")
                or item.get("workflow_name")
                or ((item.get("workflowConfiguration") or {}).get("name")),
                "status": item.get("status"),
                "agent_name": item.get("agentName"),
                "error_message": item.get("errorMessage") or item.get("errorDetails") or item.get("error"),
                "failure_time": item.get("completedDate")
                or item.get("lastUpdatedDate")
                or item.get("createdDate")
                or item.get("completed_at")
                or item.get("started_at"),
                "failure_time_display": _format_display_time(ts) if ts else "",
                "created_date": item.get("createdDate") or item.get("started_at"),
                "completed_date": item.get("completedDate") or item.get("completed_at"),
            }
        )
        if len(failures) >= limit:
            break

    def _failure_sort_key(item: dict) -> float:
        ts = _parse_timestamp(
            item.get("failure_time")
            or item.get("completed_date")
            or item.get("created_date")
        )
        return ts.timestamp() if ts else float("-inf")

    failures.sort(key=_failure_sort_key, reverse=True)

    result = {
        "failures": failures,
        "total_count": len(failures),
        "time_window_hours": hours,
    }
    if failures:
        latest = failures[0]
        workflow_label = str(latest.get("workflow_name") or "Unknown workflow").strip()
        execution_id = str(latest.get("execution_id") or "").strip()
        agent_name = str(latest.get("agent_name") or "").strip()
        failure_time_display = str(latest.get("failure_time_display") or "").strip()

        message = f"The latest workflow failure is **{workflow_label}**"
        if execution_id:
            message += f" (execution ID `{execution_id}`)"
        if agent_name:
            message += f", which failed on agent **{agent_name}**"
        if failure_time_display:
            message += f" at {failure_time_display}"
        message += "."

        result["latest_failure"] = latest
        result["message"] = message
    if not failures and last_error:
        result["warning"] = f"No recent failures found. Last endpoint error: {last_error}"
    return result


def get_system_health() -> dict:
    client = get_ae_client()
    org = str(client.default_org_code or "").strip()
    candidates = []
    if org:
        candidates.append((f"/tenants/{org}/system/health", False))
        candidates.append((f"/{org}/system/health", False))
    candidates.extend(
        [
            ("/api/v1/system/health", False),
            ("/system/health", False),
        ]
    )
    if org:
        candidates.append((f"/tenants/{org}/system/health", True))
        candidates.append((f"/{org}/system/health", True))
    candidates.append(("/system/health", True))

    last_error = None
    resp = None
    for path, use_rest_prefix in candidates:
        try:
            resp = client.request(
                "GET", path, use_rest_prefix=use_rest_prefix, silent_on_status=[400, 403, 404]
            )
            break
        except Exception as exc:
            last_error = exc

    if resp is None:
        # Fallback for common 403 Forbidden errors if platform-level health is restricted
        try:
            agents_info = get_agent_status()
            if agents_info.get("success"):
                agents = agents_info.get("agents", [])
                online = sum(1 for a in agents if a.get("agentState") in ("CONNECTED", "RUNNING"))
                return {
                    "status": "limited_health_info",
                    "agents_online": online,
                    "agents_offline": len(agents) - online,
                    "agents": agents,
                    "warning": "Primary system health API returned 403 Forbidden; showing basic agent monitoring status instead.",
                }
        except Exception:
            pass
        raise last_error or RuntimeError("Could not fetch system health")

    if resp is None or not isinstance(resp, dict):
        agents = []
        online = 0
    else:
        agents = resp.get("agents", [])
        online = sum(1 for a in agents if a.get("status") == "online")

    return {
        "status": (resp or {}).get("status", "unknown"),
        "agents_online": online,
        "agents_offline": len(agents) - online,
        "agents": agents,
        "queue_depth": resp.get("queue_depth", 0),
        "active_executions": resp.get("active_executions", 0),
    }


def get_queue_status(queue_name: str) -> dict:
    org = get_ae_client().default_org_code
    resp = get_ae_client().get(f"/{org}/queues/{queue_name}/status")
    return {
        "queue_name": queue_name,
        "pending": resp.get("pending", 0),
        "running": resp.get("running", 0),
        "completed_today": resp.get("completed_today", 0),
        "failed_today": resp.get("failed_today", 0),
    }


def get_agent_status(agent_name: str = "") -> dict:
    """Check T4 agent health via POST /monitoring/agents.

    Ref: code_ref.py t4_check_agent_status() / t4_get_agent_monitoring()
    """
    client = get_ae_client()
    agents = client.check_agent_status()

    if not agents:
        return {
            "success": False,
            "agents": [],
            "total": 0,
            "message": "No agents found or monitoring endpoint unreachable.",
        }

    if agent_name:
        agents = [a for a in agents if a.get("agentName", "") == agent_name]

    selected = next(
        (a for a in agents if a.get("agentState", "").upper() in ("CONNECTED", "RUNNING")),
        agents[0] if agents else {},
    )

    return {
        "success": True,
        "agents": agents,
        "total": len(agents),
        "selected_agent": {
            "name": selected.get("agentName", "Unknown"),
            "id": selected.get("agentId") or selected.get("id"),
            "state": selected.get("agentState", "UNKNOWN"),
        },
    }


def t4_check_agent_status_tool(agent_name: str = "") -> dict:
    """T4 Status Check Agent — mirrors code_ref.py t4_get_agent_details().

    Uses POST /monitoring/agents endpoint and selects the best available agent.
    """
    client = get_ae_client()
    agents = client.check_agent_status()

    if not agents:
        return {
            "success": False,
            "agents": [],
            "state": "NO_AGENTS",
            "message": "No agents returned. Check T4_ORG_CODE and T4 connectivity.",
        }

    if agent_name:
        named = [a for a in agents if a.get("agentName") == agent_name]
        if named:
            agents = named

    selected = next(
        (a for a in agents if a.get("agentState", "").upper() in ("CONNECTED", "RUNNING")),
        agents[0],
    )

    state = selected.get("agentState", "UNKNOWN").upper()
    is_healthy = state in ("CONNECTED", "RUNNING", "ACTIVE")

    return {
        "success": True,
        "agent_name": selected.get("agentName", "Unknown"),
        "agent_id": selected.get("agentId") or selected.get("id"),
        "agent_state": state,
        "is_healthy": is_healthy,
        "all_agents": agents,
        "message": (
            f"Agent '{selected.get('agentName')}' is {state} and healthy."
            if is_healthy
            else f"Agent '{selected.get('agentName')}' is {state} — may need attention."
        ),
    }


def t4_execute_and_poll(
    workflow_name: str,
    workflow_id: str,
    params: dict = None,
    poll_interval_sec: int = 5,
    max_poll_attempts: int = 60,
    user_id: str = "",
    org_code: str = "",
) -> dict:
    """T4 Execution Agent — execute a workflow and poll until complete.

    Ref: code_ref.py t4_execute_workflow() + t4_poll_status()
    Builds the correct T4 payload: orgCode, workflowName, params list format.
    """
    client = get_ae_client()
    # Resolve the name first for accurate schema lookup
    resolved_name = _resolve_cached_workflow_name_for_user(
        client,
        workflow_name,
        user_id=user_id,
        org_code=org_code,
    ) or workflow_name
    resolved_org = str(org_code or default_org_code()).strip()
    resolved_workflow_id = workflow_id
    if not resolved_workflow_id:
        getter = getattr(client, "get_cached_workflow_id", None)
        if callable(getter):
            try:
                resolved_workflow_id = getter(
                    resolved_name,
                    user_id=user_id,
                    org_code=resolved_org,
                )
            except TypeError:
                resolved_workflow_id = getter(resolved_name)

    if bool(user_id) or is_execute_enforced():
        if not user_id or not resolved_workflow_id or not can_execute_workflow(user_id, resolved_workflow_id, resolved_org):
            return {
                "success": False,
                "error": _workflow_access_denied_message(
                    resolved_name or workflow_name,
                    user_id=user_id, org_code=resolved_org, action="trigger",
                ),
            }
    
    # AE-77: Check for "File" type parameters. File upload is not supported in agentic chat yet.
    schema = client.get_cached_workflow_parameters(resolved_name)
    file_params = [
        p.get("name") for p in schema 
        if str(p.get("type", "")).strip().lower() in {"file", "attachment", "upload"}
    ]
    if file_params:
        logger.info(f"Workflow '{resolved_name}' requires file upload. Rejecting agentic trigger in t4_execute_and_poll.")
        return {
            "success": False,
            "error": (
                f"I've identified that the **{resolved_name}** bot requires a **document upload** "
                f"for the following parameter(s): `{', '.join(file_params)}`. \n\n"
                "This action cannot be completed through the chat yet. "
                "Please go to the **AutomationEdge (AE) server** to trigger this bot manually. "
                "Thank you!"
            ),
            "reason": f"Workflow requires file upload for: {', '.join(file_params)}",
            "workflow_name": resolved_name
        }

    required = client.get_required_parameters(resolved_name)
    missing = [p for p in required if not (params or {}).get(p)]

    if missing:
        # Build a friendly, specific question (mirrors dynamic tool behavior)
        param_bullets = "\n".join(f"  • {p}" for p in missing)
        friendly_name = workflow_name.replace("_", " ").replace("-", " ").title()
        return {
            "success": False,
            "needs_user_input": True,
            "question": (
                f"I'm ready to help with **{friendly_name}**! Just need a few specific details first:\n"
                f"{param_bullets}\n\n"
                f"Please share these and I'll take care of the rest."
            ),
            "tool_name": "t4_execute_and_poll",
            "workflow_name": workflow_name,
            "missing_params": missing
        }

    # Execute via the updated client method (handles payload format + query params automatically)
    # Pick a running agent so AE dispatches to the right one
    _picked_agent_id = ""
    _picked_agent_name = ""
    try:
        wf_assigned = _get_assigned_agents_for_workflow(client, resolved_name)
        for a in (wf_assigned or []):
            if str(a.get("agentState") or "").upper() in {"RUNNING", "CONNECTED", "ACTIVE"}:
                _picked_agent_id = str(a.get("id") or a.get("agentId") or a.get("uuid") or "")
                _picked_agent_name = str(a.get("agentName") or a.get("name") or "")
                break
    except Exception:
        pass
    try:
        execute_resp = client.execute_workflow(
            workflow_name=resolved_name,
            workflow_id=resolved_workflow_id,
            params=params,
            org_code=resolved_org,
            user_id=user_id,
            source="ae-agentic-support-status-check",
            agent_id=_picked_agent_id,
            agent_name=_picked_agent_name,
        )
    except Exception as exc:
        logger.error("T4 execute failed: %s", exc)
        return {"success": False, "error": str(exc)}

    request_id = (
        execute_resp.get("automationRequestId")
        or execute_resp.get("requestId")
        or execute_resp.get("id")
    )
    if not request_id:
        return {
            "success": False,
            "error": "T4 did not return a request/execution ID.",
            "raw": execute_resp,
        }

    logger.info(
        "T4 execute: workflow=%s request_id=%s — polling...", workflow_name, request_id
    )

    poll_result = client.poll_execution_status(
        execution_id=str(request_id),
        poll_interval_sec=poll_interval_sec,
        max_attempts=max_poll_attempts,
    )

    status = poll_result.get("status", "unknown")
    raw = poll_result.get("raw") or {}
    diagnosis = raw.get("newExecutionDiagnosis") if isinstance(raw, dict) and isinstance(raw.get("newExecutionDiagnosis"), dict) else {}
    assigned_agents = list(diagnosis.get("assigned_agents") or [])
    if not assigned_agents and str(status or "").upper() == "NO_AGENT":
        assigned_agents = _get_assigned_agents_for_workflow(client, resolved_name)
    assigned_summary = _assigned_agent_summary(assigned_agents)

    # ── Extract detailed workflowResponse ──
    detailed_msg = _extract_workflow_response_message(raw)

    status_messages = {
        "Complete": f"'{workflow_name}' completed successfully! {detailed_msg}".strip(),
        "Failure": detailed_msg or f"'{workflow_name}' encountered a failure. Check logs for details.",
        "no_agent": (
            str(diagnosis.get("summary"))
            if diagnosis.get("summary")
            else (
                f"No automation agent was available to start '{workflow_name}'. "
                f"Assigned agent(s): {assigned_summary}. "
                f"Request ID: `{request_id}`. Please start or reconnect one of these agents and retry."
                if assigned_summary
                else f"No automation agent was available to start '{workflow_name}'. Request ID: `{request_id}`. Please check agent health."
            )
        ),
        "waiting_other_process": (
            (
                f"Request ID `{request_id}` is already triggered and currently in **New** status. "
                f"{str(diagnosis.get('summary'))}"
            ).strip()
            if diagnosis.get("summary")
            else (
                f"Request ID `{request_id}` is already triggered and currently in **New** status. "
                f"'{workflow_name}' is still waiting because another process is currently running. "
                "Please wait some time and check again."
            )
        ),
        "timeout": "Execution timed out waiting for a result.",
        "Error": detailed_msg or f"'{workflow_name}' encountered an error.",
        "in_progress": poll_result.get(
            "in_progress_hint",
            f"Execution still running. Use request_id {request_id} to check status.",
        ),
    }

    result_status = "New" if status == "waiting_other_process" else status
    result = {
        "success": status in {"Complete", "waiting_other_process"},
        "status": result_status,
        "request_id": str(request_id),
        "workflow_name": workflow_name,
        "message": status_messages.get(status, f"Status: {status}"),
        "error": status_messages.get(status, f"Status: {status}") if status == "no_agent" else "",
        "agent_name": _primary_assigned_agent_name(assigned_agents),
        "assigned_agents": assigned_agents,
        "diagnosis": diagnosis.get("reason") or "",
        "other_execution_id": diagnosis.get("other_execution_id") or "",
        "other_workflow_name": diagnosis.get("other_workflow_name") or "",
        "raw": raw,
    }
    if detailed_msg:
        result["workflow_response_message"] = detailed_msg
    return result


def get_execution_status(execution_id: str, user_id: str = "", org_code: str = "") -> dict:
    """Get status of a specific workflow execution by ID.
    
    Use this when you have a numeric request_id or execution_id.
    Returns status, bot name, agent, timings, and any error message.
    """
    client = get_ae_client()
    resp = client.get_execution_status(execution_id)
    resp = _maybe_refresh_execution_payload(client, execution_id, resp)
    if (user_id or is_read_enforced()) and (
        not isinstance(resp, dict) or not _is_visible_workflow_record(client, resp, user_id, org_code)
    ):
        return {
            "execution_id": execution_id,
            "status": "UNAUTHORIZED",
            "error_message": _workflow_access_denied_message(),
            "message": _workflow_access_denied_message(),
        }
    status = resp.get("status", "UNKNOWN")
    workflow_name = _extract_workflow_ref(resp)
    diagnosis = _get_new_status_diagnosis(client, resp, execution_id=execution_id)
    start_time = resp.get("startTime") or resp.get("createdDate")
    start_ts = _parse_timestamp(start_time)
    detail_msg = _extract_workflow_response_message(resp)
    message = (
        str(diagnosis.get("summary"))
        if diagnosis.get("summary")
        else (
            detail_msg
            if detail_msg and str(status or "").strip().upper() in {"COMPLETE", "FAILURE", "ERROR"}
            else (
            f"Execution `{execution_id}` for **{workflow_name or 'this workflow'}** is currently **{status}**."
            if workflow_name
            else f"Execution `{execution_id}` is currently **{status}**."
            )
        )
    )

    resolved_org = str(org_code or default_org_code()).strip()
    related_issue_check = _maybe_add_related_issue_health_check(resp, org_code=resolved_org)
    if related_issue_check and related_issue_check.get("message"):
        message = _build_related_issue_status_message(
            workflow_name or "this workflow",
            start_ts,
            related_issue_check,
            org_code=resolved_org,
        )
    
    # Enrich the response for LLM decision making
    result = {
        "execution_id": execution_id,
        "status": status,
        "workflow_name": workflow_name,
        "agent_name": resp.get("agentName"),
        "start_time": start_time,
        "end_time": resp.get("endTime") or resp.get("lastUpdatedDate"),
        "error_message": resp.get("errorMessage") or resp.get("errorDetails") or detail_msg or resp.get("workflowResponse"),
        "message": message,
        "raw": resp,
        "recommendation": (
            str(diagnosis.get("summary"))
            if diagnosis.get("summary")
            else (
                "The workflow has already reported its outcome above. Use execution logs only if you need deeper technical details."
                if detail_msg and str(status or "").strip().upper() in {"COMPLETE", "COMPLETED", "FAILURE", "FAILED", "ERROR"}
                else (
                f"Use 'get_execution_logs' with execution_id '{execution_id}' to see technical details/errors."
                if status in ("Failure", "Error", "Complete")
                else "Execution is still in progress."
                )
            )
        ),
    }
    if detail_msg:
        result["workflow_response_message"] = detail_msg
    if diagnosis:
        result["diagnosis"] = diagnosis.get("reason")
        result["assigned_agents"] = diagnosis.get("assigned_agents") or []
        result["other_execution_id"] = diagnosis.get("other_execution_id") or ""
        result["other_workflow_name"] = diagnosis.get("other_workflow_name") or ""
    if related_issue_check:
        result["related_issue_check"] = related_issue_check
        if related_issue_check.get("failure_reason"):
            result["failure_reason"] = related_issue_check.get("failure_reason")
    return result


# ── Register tools ──

tool_registry.register(
    ToolDefinition(
        name="t4_execute_and_poll",
        description=(
            "T4 Execution Agent: Execute a specific T4 workflow by name and ID, "
            "then poll until it completes (Complete/Failure/Error). "
            "Returns final status, request ID, and result message. "
            "Use this when the user wants to RUN or TRIGGER an automation workflow."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "The exact T4 workflow name",
            },
            "workflow_id": {
                "type": "string",
                "description": "The numeric T4 workflow ID",
            },
            "params": {
                "type": "object",
                "description": "Key-value dict of workflow input parameters",
            },
            "poll_interval_sec": {
                "type": "integer",
                "description": "Seconds between status polls (default 5)",
            },
            "max_poll_attempts": {
                "type": "integer",
                "description": "Max polls before giving up (default 60)",
            },
        },
        required_params=["workflow_name", "workflow_id"],
        always_available=True,
    ),
    t4_execute_and_poll,
)

tool_registry.register(
    ToolDefinition(
        name="t4_check_agent_status",
        description=(
            "T4 Status Check Agent: Check if a T4 automation agent is RUNNING/CONNECTED "
            "using the POST /monitoring/agents endpoint. "
            "Returns agent name, ID, state, and is_healthy flag. "
            "Use this when the user asks 'is my agent running?' or 'check agent health'."
        ),
        category="status",
        tier="read_only",
        parameters={
            "agent_name": {
                "type": "string",
                "description": "Agent name to check (empty = check first/best agent)",
            },
        },
        required_params=[],
        always_available=True,
    ),
    t4_check_agent_status_tool,
)

tool_registry.register(
    ToolDefinition(
        name="get_execution_status",
        description=(
            "Get detailed status of a specific workflow execution or numeric request ID (e.g. 2501865). "
            "Checks both global and org-scoped T4 workflowinstances endpoints. "
            "Use this when the user provides a specific investigation target by ID."
        ),
        category="status",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The automation request ID / execution ID to track",
            },
        },
        required_params=["execution_id"],
        always_available=True,
    ),
    get_execution_status,
)

tool_registry.register(
    ToolDefinition(
        name="check_workflow_status",
        description=(
            "Check the CURRENT, HISTORICAL, or EXECUTION status/records of bots (workflows). "
            "Returns the absolute latest execution details (even if old), request ID, "
            "and a 24-hour summary. Use this to find out 'did my bot run', 'is it failing', "
            "'what was the last status', 'what is the execution status', or 'how many times did it run today'. "
            "When the status API already includes a workflow response message or error, surface that first and use logs only for deeper technical analysis."
        ),
        category="status",
        tier="read_only",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Name of the workflow/bot to check (e.g. 'Maturity Claim' or 'Email Bot JD'). Handles natural language names.",
            },
            "status": {
                "type": "string",
                "description": "Optional: Filter history by status (e.g., 'Complete', 'Failure', 'New', 'InProgress')",
            },
        },
        required_params=[],
        always_available=True,
        use_when="The user asks about the status, history, last run, or current state of any bot or workflow.",
        avoid_when="The user explicitly wants to EXECUTE, RUN, or TRIGGER a new workflow instance.",
    ),
    check_workflow_status,
)

tool_registry.register(
    ToolDefinition(
        name="list_recent_failures",
        description=(
            "List all failed workflow executions within a specified time "
            "window. Useful for identifying patterns or cascade failures."
        ),
        category="status",
        tier="read_only",
        parameters={
            "hours": {
                "type": "integer",
                "description": "Time window in hours (default 24)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20)",
            },
        },
        required_params=[],
        always_available=True,
    ),
    list_recent_failures,
)

tool_registry.register(
    ToolDefinition(
        name="get_system_health",
        description=(
            "Get overall AutomationEdge platform health: agent counts, "
            "queue totals, stuck items, workflow stats."
        ),
        category="status",
        tier="read_only",
        parameters={},
        required_params=[],
        always_available=True,
    ),
    get_system_health,
)

tool_registry.register(
    ToolDefinition(
        name="get_queue_status",
        description=(
            "Check queue depth, processing rate, and stuck items "
            "for a specific queue."
        ),
        category="status",
        tier="read_only",
        parameters={
            "queue_name": {
                "type": "string",
                "description": "Name of the queue to check",
            },
        },
        required_params=["queue_name"],
    ),
    get_queue_status,
)

tool_registry.register(
    ToolDefinition(
        name="get_agent_status",
        description=(
            "Check if T4 AE agents/bots are online using the T4 monitoring API. "
            "Returns agent state (RUNNING/CONNECTED/STOPPED) for all or a named agent."
        ),
        category="status",
        tier="read_only",
        parameters={
            "agent_name": {
                "type": "string",
                "description": "Agent name (empty for all agents)",
            },
        },
        required_params=[],
    ),
    get_agent_status,
)



def list_workflows(limit: int = 100, user_id: str = "", org_code: str = "") -> dict:
    """List all available AutomationEdge workflows."""
    try:
        client = get_ae_client()
        workflows = client.list_workflows(page_size=limit)
        if user_id or is_read_enforced():
            workflows = [
                item for item in workflows
                if isinstance(item, dict) and _is_visible_workflow_record(client, item, user_id, org_code)
            ]
        items = []
        for w in workflows:
            items.append({
                "workflow_id": w.get("workflowId") or w.get("id"),
                "workflow_name": w.get("workflowName") or w.get("name"),
                "description": w.get("description"),
                "active": w.get("active", True),
            })
        return {"workflows": items, "count": len(items)}
    except Exception as exc:
        logger.error("list_workflows failed: %s", exc)
        return {"error": str(exc), "workflows": [], "count": 0}


tool_registry.register(
    ToolDefinition(
        name="ae.workflow.list",
        description="List all available AutomationEdge workflows.",
        category="dependency",
        tier="read_only",
        parameters={
            "limit": {
                "type": "integer",
                "description": "Max number of workflows to return",
                "default": 100,
            }
        },
        required_params=[],
    ),
    list_workflows,
)
