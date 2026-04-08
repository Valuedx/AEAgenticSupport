"""
Remediation tools — restart, trigger, requeue operations.
Higher-risk actions require approval via the ApprovalGate.

CHANGES in trigger_workflow (v2):
  1. Better parameter collection UX       — batch-collects ALL missing params in one shot,
                                            with per-param type hints and examples.
  2. Smarter workflow name resolution      — fuzzy/alias resolution before schema lookup;
                                            unknown names surface a friendly suggestion.
  3. Improved status/response messaging   — distinct human-readable messages for every
                                            terminal and non-terminal state; no raw dict dumps.
  4. Approval gate flow improvements      — clean, card-style confirmation message instead of
                                            raw dict; risk badge and parameter table.
  5. File-parameter early exit            — detected BEFORE asking for any other params so the
                                            user is never asked for inputs they can't supply.
  6. Agent-status pre-check               — ensures at least one assigned automation agent is
                                            active before proceeding with parameter collection.
"""

import logging

from config.settings import CONFIG
from security.workflow_access import (
    can_execute_workflow,
    default_org_code,
    get_user_accessible_workflow_names,
    is_execute_enforced,
)
from tools.base import AutomationEdgeClient, ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.remediation")


def _extract_workflow_response_message(record: dict | None) -> str:
    try:
        return str(AutomationEdgeClient.extract_workflow_response_message(record) or "").strip()
    except Exception:
        return ""


def _extract_new_execution_ref(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(
        payload.get("new_execution_id")
        or payload.get("newExecutionId")
        or payload.get("new_request_id")
        or payload.get("newRequestId")
        or payload.get("automationRequestId")
        or payload.get("requestId")
        or payload.get("executionId")
        or payload.get("id")
        or ""
    ).strip()


def _resolve_execution_status_context(
    client,
    execution_id: str,
    *,
    workflow_name: str = "",
    poll_interval_sec: int = 2,
    max_attempts: int = 15,
    poll_result: dict | None = None,
) -> dict:
    """Get status/raw/message for an execution with terminal payload enrichment.

    This keeps logic generic and reusable across trigger/restart/resubmit flows
    without workflow-specific hardcoding.
    """
    request_id = str(execution_id or "").strip()
    if not request_id:
        return {"status": "", "raw": {}, "detail_msg": "", "poll_result": {}}

    result = poll_result if isinstance(poll_result, dict) else {}
    if not result and hasattr(client, "poll_execution_status"):
        try:
            result = client.poll_execution_status(
                execution_id=request_id,
                poll_interval_sec=poll_interval_sec,
                max_attempts=max_attempts,
            )
        except Exception as exc:
            logger.warning("Status poll failed for execution %s: %s", request_id, exc)
            result = {}

    status = str((result or {}).get("status") or "").strip()
    raw = (result or {}).get("raw")
    raw = raw if isinstance(raw, dict) else {}
    detail_msg = _extract_workflow_response_message(raw)

    if (
        str(status).upper() == "COMPLETE"
        and not detail_msg
        and hasattr(client, "refresh_execution_payload")
    ):
        try:
            refreshed = client.refresh_execution_payload(
                request_id,
                record=raw or None,
                workflow_name=workflow_name,
            )
            if isinstance(refreshed, dict):
                raw = refreshed
                detail_msg = _extract_workflow_response_message(raw)
                refreshed_status = str(raw.get("status") or "").strip()
                if refreshed_status:
                    status = refreshed_status
        except Exception as exc:
            logger.debug("Terminal payload refresh failed for execution %s: %s", request_id, exc)

    return {
        "status": status,
        "raw": raw,
        "detail_msg": detail_msg,
        "poll_result": result if isinstance(result, dict) else {},
    }


def _build_trigger_access_message(
    workflow_name: str,
    available: list[str] | None = None,
) -> str:
    clean_name = str(workflow_name or "this workflow").strip() or "this workflow"
    choices = [str(item or "").strip() for item in (available or []) if str(item or "").strip()]
    if choices:
        wf_list = "\n".join(f"  • `{name}`" for name in choices)
        return (
            f"I’m unable to start **{clean_name}** with the workflow access currently assigned to your account.\n\n"
            f"The workflows currently available to your account are:\n{wf_list}\n\n"
            "Please select one from this list, or ask an AutomationEdge administrator to review your workflow access."
        )
    return (
        f"I’m unable to start **{clean_name}** because no eligible workflow access is currently available for your account.\n\n"
        "Please contact an AutomationEdge administrator to review and update your workflow permissions."
    )


# ---------------------------------------------------------------------------
# Pre-trigger health gate — blocks re-trigger when upstream systems are down
# ---------------------------------------------------------------------------

class _HealthGateBlockError(Exception):
    """Raised when the pre-trigger health gate blocks a workflow trigger."""


def _run_both_health_checks(client) -> dict:
    """Run Life Asia AND TEBT health check workflows, return combined summary.

    Returns dict with keys: life_asia, tebt — each containing a result dict
    from _run_related_health_check (status, message, request_id, etc.).
    """
    from tools.status_tools import _run_related_health_check

    results = {}
    success_statuses = {"COMPLETE", "COMPLETED", "SUCCESS", "SUCCEEDED"}

    # Life Asia health check
    la_wf = str(CONFIG.get("LIFE_ASIA_HEALTH_CHECK_WORKFLOW", "") or "").strip()
    if la_wf:
        la_info = {
            "issue_type": "life_asia",
            "issue_label": "Life Asia",
            "health_check_label": "Life Asia health check",
            "workflow_name": la_wf,
        }
        try:
            la_result = _run_related_health_check(client, la_info)
            la_result["passed"] = str(la_result.get("status", "")).upper() in success_statuses
            results["life_asia"] = la_result
        except Exception as exc:
            logger.warning("Life Asia health check failed to execute: %s", exc)
            results["life_asia"] = {
                "passed": False,
                "status": "ERROR",
                "message": f"Life Asia health check could not be triggered: {exc}",
            }

    # TEBT health check
    tebt_wf = str(CONFIG.get("TEBT_HEALTH_CHECK_WORKFLOW", "") or "").strip()
    if tebt_wf:
        tebt_info = {
            "issue_type": "tebt",
            "issue_label": "TEBT",
            "health_check_label": "TEBT health check",
            "workflow_name": tebt_wf,
        }
        try:
            tebt_result = _run_related_health_check(client, tebt_info)
            tebt_result["passed"] = str(tebt_result.get("status", "")).upper() in success_statuses
            results["tebt"] = tebt_result
        except Exception as exc:
            logger.warning("TEBT health check failed to execute: %s", exc)
            results["tebt"] = {
                "passed": False,
                "status": "ERROR",
                "message": f"TEBT health check could not be triggered: {exc}",
            }

    return results


def _pre_trigger_health_gate(client, workflow_name: str, org_code: str) -> dict | None:
    """Check recent executions for Life Asia/TEBT failures before allowing a trigger.

    Scans the last 5 executions to find the most recent *terminal* (finished)
    state.  If that terminal execution FAILED due to Life Asia or TEBT issues
    (and is not older than 4 hours), runs BOTH health check workflows.

    Returns:
        None  – no block, gate is open
        dict  – health checks passed; summary dict for the caller to attach
    Raises:
        _HealthGateBlockError – at least one health check failed; trigger blocked
    """
    from datetime import datetime, timezone, timedelta
    from tools.status_tools import (
        _get_workflow_instances_compat,
        _detect_related_issue,
    )
    from tools.log_tools import get_execution_logs

    # ── 1. Fetch recent executions from the AE server (live API, not cache) ──
    try:
        instances = _get_workflow_instances_compat(client, workflow_name, limit=5)
    except Exception as exc:
        logger.warning("Health gate: could not fetch executions for %s: %s", workflow_name, exc)
        return None  # Non-blocking: if we can't check, allow the trigger

    if not instances:
        return None  # First-ever execution — nothing to check

    # ── 2. Find the most recent TERMINAL execution ───────────────────────────
    # Terminal = finished states.  Skip QUEUED / IN_PROGRESS / RUNNING / PENDING
    # because those haven't produced a result yet.
    terminal_statuses = {
        "COMPLETE", "COMPLETED", "SUCCESS", "SUCCEEDED",
        "FAILURE", "FAILED", "ERROR", "TERMINATED", "CANCELLED",
    }
    failure_statuses = {"FAILURE", "FAILED", "ERROR"}

    latest_terminal = None
    for inst in instances:
        status = str(inst.get("status") or "").strip().upper()
        if status in terminal_statuses:
            latest_terminal = inst
            break  # instances are sorted newest-first by AE API

    if latest_terminal is None:
        return None  # All recent runs are still in-progress — skip gate

    terminal_status = str(latest_terminal.get("status") or "").strip().upper()
    if terminal_status not in failure_statuses:
        return None  # Most recent finished run was a success — no concern

    # ── 3. Staleness check — ignore failures older than 4 hours ──────────────
    try:
        created_raw = latest_terminal.get("createdDate") or latest_terminal.get("started_at")
        if created_raw:
            if isinstance(created_raw, (int, float)):
                created_dt = datetime.fromtimestamp(created_raw / 1000, tz=timezone.utc)
            else:
                created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - created_dt
            if age > timedelta(hours=4):
                logger.info(
                    "Health gate: failure for %s is %.1f hours old — skipping gate",
                    workflow_name, age.total_seconds() / 3600,
                )
                return None
    except Exception as exc:
        logger.debug("Health gate: could not parse timestamp for staleness check: %s", exc)
        # If we can't parse the timestamp, proceed with the gate anyway

    # ── 4. Fetch logs of the failed execution to detect issue type ───────────
    execution_id = (
        latest_terminal.get("id")
        or latest_terminal.get("automationRequestId")
        or latest_terminal.get("requestId")
        or ""
    )
    if not execution_id:
        return None

    try:
        log_result = get_execution_logs(str(execution_id), tail=0)
    except Exception as exc:
        logger.warning("Health gate: log fetch failed for execution %s: %s", execution_id, exc)
        return None  # Non-blocking

    # ── 5. Detect if failure is Life Asia or TEBT related ────────────────────
    issue_info = _detect_related_issue(log_result)
    if not issue_info:
        return None  # Not a Life Asia/TEBT issue — skip gate

    issue_label = issue_info.get("issue_label", "")
    logger.info(
        "Health gate: execution %s of %s failed due to %s issue — running both health checks",
        execution_id, workflow_name, issue_label,
    )

    # ── 6. Run BOTH health checks ────────────────────────────────────────────
    health_results = _run_both_health_checks(client)

    # If no health checks are configured at all, skip the gate
    if not health_results:
        logger.warning(
            "Health gate: Life Asia/TEBT issue detected but no health check workflows configured"
        )
        return None

    # ── 7. Build summary message ─────────────────────────────────────────────
    lines = []
    all_passed = True
    for key, label in [("life_asia", "Life Asia"), ("tebt", "TEBT")]:
        result = health_results.get(key)
        if not result:
            lines.append(f"  \u2022 {label} health check: \u26a0\ufe0f Not configured")
            continue
        passed = result.get("passed", False)
        msg = result.get("message", "")
        icon = "\u2705" if passed else "\u274c"
        lines.append(f"  {icon} {msg}")
        if not passed:
            all_passed = False

    health_summary = "\n".join(lines)
    friendly = workflow_name.replace("_", " ").replace("-", " ").title()

    if not all_passed:
        raise _HealthGateBlockError(
            f"The last execution of **{friendly}** failed due to a **{issue_label}** issue.\n\n"
            f"I ran health checks before retrying:\n{health_summary}\n\n"
            f"Please resolve the issue before retrying this workflow."
        )

    # Both passed — return summary so caller can attach to the response
    logger.info("Health gate: both checks passed for %s — allowing trigger", workflow_name)
    return {
        "health_gate_passed": True,
        "health_summary": (
            f"The last execution failed due to a **{issue_label}** issue, "
            f"but health checks confirm systems are back up:\n{health_summary}"
        ),
        "results": health_results,
    }


def _resolve_cached_workflow_name_for_user(client, workflow_name: str, user_id: str = "", org_code: str = "") -> str:
    if is_execute_enforced() and not str(user_id or "").strip():
        return ""
    resolver = getattr(client, "resolve_cached_workflow_name")
    try:
        resolved = resolver(workflow_name, user_id=user_id, org_code=org_code)
    except TypeError:
        resolved = resolver(workflow_name)
    if resolved:
        return resolved

    specificity_checker = getattr(client, "is_specific_workflow_lookup_query", None)
    if callable(specificity_checker):
        try:
            if not specificity_checker(workflow_name):
                return ""
        except Exception:
            return ""

    fuzzy_resolver = getattr(client, "resolve_workflow_name_from_text", None)
    if callable(fuzzy_resolver):
        try:
            return fuzzy_resolver(
                workflow_name,
                user_id=user_id,
                org_code=org_code,
                require_execute=True,
            )
        except TypeError:
            return fuzzy_resolver(
                workflow_name,
                user_id=user_id,
                org_code=org_code,
            )
    return ""


def _get_cached_workflow_info_for_user(client, workflow_name: str, user_id: str = "", org_code: str = "") -> tuple[str, list[dict]]:
    if is_execute_enforced() and not str(user_id or "").strip():
        return ("", [])
    getter = getattr(client, "get_cached_workflow_info", None)
    if not callable(getter):
        return ("", [])
    try:
        value = getter(workflow_name, user_id=user_id, org_code=org_code)
    except TypeError:
        value = getter(workflow_name)
    if isinstance(value, tuple) and len(value) >= 2:
        return (str(value[0] or ""), list(value[1] or []))
    return ("", [])


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


def _running_assigned_agents(agents: list[dict]) -> list[dict]:
    return [
        agent for agent in (agents or [])
        if str(agent.get("agentState") or agent.get("state") or "").strip().upper()
        in {"RUNNING", "CONNECTED", "ACTIVE"}
    ]


def _extract_workflow_name_from_execution_status(status_resp: dict | None) -> str:
    if not isinstance(status_resp, dict):
        return ""
    workflow_meta = status_resp.get("workflowConfiguration") or {}
    return str(
        status_resp.get("workflowName")
        or status_resp.get("workflow_name")
        or workflow_meta.get("name")
        or ""
    ).strip()


def _extract_agent_reference(payload: dict | None) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("", "")
    agent_meta = payload.get("agentDetails") or payload.get("agent") or {}
    agent_name = str(
        payload.get("agentName")
        or payload.get("agent_name")
        or agent_meta.get("agentName")
        or agent_meta.get("name")
        or ""
    ).strip()
    agent_id = str(
        payload.get("agentId")
        or payload.get("agent_id")
        or payload.get("uuid")
        or payload.get("id")
        or agent_meta.get("agentId")
        or agent_meta.get("id")
        or ""
    ).strip()
    return (agent_name, agent_id)


def _get_request_payload(execution_id: str) -> dict:
    try:
        from mcp_server.ae_client import get_ae_client as get_mcp_client

        request_payload = get_mcp_client().get_request(execution_id)
        return request_payload if isinstance(request_payload, dict) else {}
    except Exception as exc:
        logger.debug("Could not load request payload for %s: %s", execution_id, exc)
        return {}


def _find_live_agent_match(client, *, agent_name: str, agent_id: str) -> dict:
    try:
        agents = client.list_agents() if hasattr(client, "list_agents") else []
    except Exception as exc:
        logger.warning("Could not list live agents while checking %s/%s: %s", agent_name, agent_id, exc)
        return {}

    for agent in agents or []:
        if not isinstance(agent, dict):
            continue
        live_id = str(agent.get("agentId") or agent.get("id") or agent.get("uuid") or "").strip()
        live_name = str(agent.get("agentName") or agent.get("name") or "").strip()
        if agent_id and live_id and live_id == agent_id:
            return agent
        if agent_name and live_name and live_name.lower() == agent_name.lower():
            return agent
    return {}


def _normalize_execution_status(raw_status: str) -> str:
    status = str(raw_status or "").strip().upper()
    if status in {"FAILURE", "FAILED", "ERROR"}:
        return "FAILED"
    if status in {"COMPLETE", "COMPLETED", "SUCCESS"}:
        return "COMPLETED"
    if status in {"RUNNING", "IN_PROGRESS", "IN PROGRESS", "PROCESSING"}:
        return "RUNNING"
    if status in {"QUEUED", "NEW", "PENDING"}:
        return "QUEUED"
    return status or "UNKNOWN"


def _guard_failed_execution_only(
    client,
    execution_id: str,
    action_name: str,
    status_resp: dict | None = None,
) -> dict | None:
    """Allow restart/resubmit only for failed executions when status is available."""
    try:
        if status_resp is None and not hasattr(client, "get_execution_status"):
            return None
        if status_resp is None:
            status_resp = client.get_execution_status(execution_id)
    except Exception as exc:
        logger.warning("Could not verify execution status for %s before %s: %s", execution_id, action_name, exc)
        return None

    status = _normalize_execution_status(
        (status_resp or {}).get("status")
        or (status_resp or {}).get("workflowStatus")
        or (status_resp or {}).get("state")
    )
    workflow_name = (
        (status_resp or {}).get("workflowName")
        or (status_resp or {}).get("workflow_name")
        or "this workflow"
    )

    if status == "FAILED":
        return None

    if status == "COMPLETED":
        return {
            "success": False,
            "error": (
                f"Execution `{execution_id}` for **{workflow_name}** is already completed, "
                f"so {action_name} is not allowed."
            ),
            "hint": "Use Fresh Run to run this workflow again.",
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "status": status,
        }

    return {
        "success": False,
        "error": (
            f"Execution `{execution_id}` for **{workflow_name}** is currently `{status}`, "
            f"so {action_name} is only allowed when the execution has failed."
        ),
        "hint": "Please use restart or resubmit only for failed executions.",
        "execution_id": execution_id,
        "workflow_name": workflow_name,
        "status": status,
    }


def _guard_assigned_agents_running(
    client,
    workflow_name: str,
    execution_id: str,
    action_name: str,
    status_resp: dict | None = None,
) -> dict | None:
    clean_workflow = str(workflow_name or "").strip()
    action_label = str(action_name or "this action").strip().capitalize()

    def _blocked_response(agents: list[dict], *, workflow_label: str, source_label: str) -> dict:
        assigned_summary = _assigned_agent_summary(agents) or "No agents assigned"
        logger.info(
            "%s blocked for execution_id=%s workflow=%s because %s agent check found no running agents: %s",
            action_label,
            execution_id,
            workflow_label,
            source_label,
            assigned_summary,
        )
        message = (
            f"{action_label} cannot continue for **{workflow_label}** because all assigned agents are currently offline or stopped "
            f"({assigned_summary}). Please start at least one assigned agent first, then try again."
        )
        return {
            "success": False,
            "error": message,
            "message": message,
            "hint": "Please start the assigned agent first, then retry this action.",
            "execution_id": execution_id,
            "workflow_name": workflow_label,
            "agent_name": _primary_assigned_agent_name(agents),
            "assigned_agents": agents,
            "agent_status": "UNAVAILABLE",
            "status": "AGENT_UNAVAILABLE",
        }

    if clean_workflow and clean_workflow != "Unknown":
        try:
            assigned_agents = _get_assigned_agents_for_workflow(client, clean_workflow)
        except Exception as exc:
            logger.warning(
                "Could not verify assigned agents for %s before %s on %s: %s",
                clean_workflow,
                action_name,
                execution_id,
                exc,
            )
            assigned_agents = []

        if assigned_agents:
            if _running_assigned_agents(assigned_agents):
                logger.info(
                    "%s allowed for execution_id=%s workflow=%s because workflow-agent mapping has a running agent.",
                    action_label,
                    execution_id,
                    clean_workflow,
                )
                return None
            return _blocked_response(
                assigned_agents,
                workflow_label=clean_workflow,
                source_label="workflow mapping",
            )

    # ── Single-agent fallback ──────────────────────────────────────────────
    # When the workflow-level mapping was skipped (unknown name) or returned
    # empty, fall back to the execution's own agent reference.  Even here,
    # if the resolved single agent is offline we still cross-check the
    # workflow-level mapping before blocking (multi-agent support).
    request_payload = _get_request_payload(execution_id)
    agent_name, agent_id = _extract_agent_reference(status_resp)
    if not (agent_name or agent_id):
        request_agent_name, request_agent_id = _extract_agent_reference(request_payload)
        agent_name = agent_name or request_agent_name
        agent_id = agent_id or request_agent_id

    live_agent = _find_live_agent_match(
        client,
        agent_name=agent_name,
        agent_id=agent_id,
    )
    if not live_agent:
        logger.info(
            "%s could not resolve a live assigned agent for execution_id=%s workflow=%s agent_name=%s agent_id=%s",
            action_label,
            execution_id,
            clean_workflow or _extract_workflow_name_from_execution_status(request_payload) or "Unknown",
            agent_name or "Unknown",
            agent_id or "Unknown",
        )
        return None

    live_state = str(live_agent.get("agentState") or live_agent.get("state") or "UNKNOWN").strip().upper()
    live_name = str(
        live_agent.get("agentName")
        or live_agent.get("name")
        or agent_name
        or agent_id
        or "Unknown"
    ).strip()
    workflow_label = clean_workflow or _extract_workflow_name_from_execution_status(request_payload) or "this workflow"
    if live_state in {"RUNNING", "CONNECTED", "ACTIVE"}:
        logger.info(
            "%s allowed for execution_id=%s workflow=%s because fallback assigned agent %s is %s.",
            action_label,
            execution_id,
            workflow_label,
            live_name,
            live_state,
        )
        return None

    # The execution's own agent is offline.  Before blocking, try the
    # workflow-level agent mapping one more time (the earlier attempt may
    # have been skipped because workflow_name was "Unknown" at that point,
    # but we may have resolved it from the request payload since then).
    resolved_wf = workflow_label if workflow_label != "this workflow" else ""
    if resolved_wf:
        try:
            wf_assigned = _get_assigned_agents_for_workflow(client, resolved_wf)
            if wf_assigned and _running_assigned_agents(wf_assigned):
                logger.info(
                    "%s allowed for execution_id=%s workflow=%s — fallback agent %s is %s "
                    "but another workflow-assigned agent is running.",
                    action_label,
                    execution_id,
                    resolved_wf,
                    live_name,
                    live_state,
                )
                return None
        except Exception as wf_exc:
            logger.warning(
                "Secondary workflow-agent check failed for %s: %s",
                resolved_wf,
                wf_exc,
            )

    return _blocked_response(
        [{"agentName": live_name, "agentState": live_state, "agentId": agent_id or live_agent.get("agentId") or live_agent.get("id")}],
        workflow_label=workflow_label,
        source_label="execution agent fallback",
    )


# ─────────────────────────────────────────────────────────────────────────────
# trigger_workflow helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_approval_message(workflow_name: str, parameters: dict, risk: str = "medium_risk") -> str:
    """
    Return a clean, card-style approval prompt instead of a raw dict dump.

    Example output
    ──────────────
    🤖 **Ready to trigger: Death_Claim**
    🟡 Risk level: Medium

    | Parameter  | Value                                              |
    |------------|----------------------------------------------------|
    | Input_Path | C:\\Users\\omkar.patil\\Downloads\\cti_template.csv |

    Reply **approve** to proceed or **reject** to cancel.
    """
    risk_labels = {
        "low_risk":    "Low",
        "medium_risk": "Medium",
        "high_risk":   "High",
    }
    risk_display = risk_labels.get(risk, "🟡 Medium")

    if parameters:
        rows = "\n".join(f"| `{k}` | {v} |" for k, v in parameters.items())
        param_table = (
            "\n| Parameter | Value |\n"
            "|-----------|-------|\n"
            f"{rows}\n"
        )
    else:
        param_table = "\n_No parameters required._\n"

    return (
        f"**Ready to trigger: {workflow_name}**\n"
        f"Risk level: {risk_display}\n"
        f"{param_table}\n"
        "Reply **approve** to proceed or **reject** to cancel."
    )


def _friendly_status_message(
    status: str,
    workflow_name: str,
    req_id,
    detail_msg: str = "",
    healthy_agent=None,
    assigned_agents: list[dict] | None = None,
) -> str:
    """Map any raw AE status string to a human-readable sentence."""
    s = str(status or "").upper()

    if s == "COMPLETE":
        return detail_msg or f"**{workflow_name}** completed successfully. (Request ID: `{req_id}`)"

    if s in {"FAILURE", "ERROR"}:
        base = detail_msg or f"**{workflow_name}** failed during execution."
        return (
            f"{base}\n\n"
            f"Request ID: `{req_id}`. "
            "You can retry with `restart_execution` or `resubmit_execution`."
        )

    if s == "IN_PROGRESS":
        return (
            f"**{workflow_name}** is currently running. "
            f"Request ID: `{req_id}`. Check back shortly for the final status."
        )

    if s == "TIMEOUT":
        return (
            f"**{workflow_name}** timed out waiting for a status update. "
            f"Request ID: `{req_id}`. "
            "The bot may still be running — please verify in the AE portal."
        )

    if s == "NO_AGENT":
        if detail_msg:
            return detail_msg
        assigned_summary = _assigned_agent_summary(list(assigned_agents or []))
        if assigned_summary:
            return (
                f"**{workflow_name}** could not start because none of its assigned automation agents are currently available. "
                f"Assigned agent(s): {assigned_summary}. "
                f"Request ID: `{req_id}`. Please start or reconnect one of these agents, then retry."
            )
        return (
            f"**{workflow_name}** could not start — no automation agent is currently available. "
            f"Request ID: `{req_id}`. Please ask your administrator to start or reconnect an agent, then retry."
        )

    if s == "WAITING_OTHER_PROCESS":
        return detail_msg or (
            f"**{workflow_name}** request is already created and currently in **New** status "
            f"because another process is currently running. "
            f"Request ID: `{req_id}`. Please wait some time and check again."
        )

    # QUEUED / PENDING / NEW or anything unrecognised
    if healthy_agent:
        return (
            f"**{workflow_name}** has been accepted and is queued for execution. "
            f"Request ID: `{req_id}`. An agent is online; it should start shortly."
        )
    return (
        f"**{workflow_name}** has been queued. Request ID: `{req_id}`. "
        "No active agent was detected — contact your administrator if it doesn't start soon."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Remediation functions
# ─────────────────────────────────────────────────────────────────────────────

def restart_execution(execution_id: str,
                      workflow_name: str = "Unknown",
                      from_checkpoint: bool = True,
                      reason: str = "Restarted by support agent",
                      requested_by: str = None,
                      case_id: str = None,
                      dry_run: bool = False) -> dict:
    """Restart a failed execution."""
    if workflow_name != "Unknown" and workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"Workflow '{workflow_name}' is protected and cannot be "
                f"restarted automatically. Escalate to the operations team."
            ),
        }

    if dry_run:
        return {
            "success": True,
            "message": f"[DRY RUN] Would restart request {execution_id}",
            "dry_run": True
        }

    client = get_ae_client()
    status_resp = None
    try:
        if hasattr(client, "get_execution_status"):
            status_resp = client.get_execution_status(execution_id)
    except Exception as exc:
        logger.warning("Could not resolve execution context for %s before restart: %s", execution_id, exc)
        status_resp = None

    status_guard = _guard_failed_execution_only(
        client,
        execution_id,
        "restart",
        status_resp=status_resp,
    )
    if status_guard:
        return status_guard

    # 1. Resolve workflow name (internal use/protection only)
    resolved_workflow_name = _extract_workflow_name_from_execution_status(status_resp)
    if resolved_workflow_name:
        workflow_name = resolved_workflow_name
        logger.info(f"Resolved workflow name for {execution_id}: {workflow_name}")

    agent_guard = _guard_assigned_agents_running(
        client,
        workflow_name,
        execution_id,
        "restart",
        status_resp=status_resp,
    )
    if agent_guard:
        return agent_guard

    # 2. Check Protection
    if workflow_name != "Unknown" and workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"Workflow '{workflow_name}' is protected and cannot be "
                f"restarted automatically. Escalate to the operations team."
            ),
        }

    # 3. Call updated restart_request (uses PUT /restart)
    try:
        resp = client.restart_request(execution_id, reason=reason)

        # Check for T4 success property
        if not resp.get("success", True):
            error_msg = resp.get("errorMessage") or resp.get("message") or "T4 returned failure."
            return {
                "success": False,
                "error": f"Restart failed: {error_msg}",
                "raw": resp
            }

        status_context = _resolve_execution_status_context(
            client,
            str(execution_id),
            workflow_name=workflow_name,
            poll_interval_sec=2,
            max_attempts=15,
        )
        final_status = status_context.get("status") or ""
        poll_raw = status_context.get("raw") or {}
        detail_msg = status_context.get("detail_msg") or ""

        result = {
            "success": True,
            "message": detail_msg or resp.get("message") or f"Request {execution_id} has been restarted",
            "execution_id": execution_id,
            "request_id": execution_id,
            "workflow_name": workflow_name,
            "raw": poll_raw or resp,
        }
        if final_status:
            result["status"] = final_status
        if detail_msg:
            result["workflow_response_message"] = detail_msg
        return result
    except Exception as e:
        err_str = str(e)
        # AE-2624: T4 restart limit reached (max 10 restarts per instance)
        # Automatically fall back to resubmit which creates a fresh execution
        if "AE-2624" in err_str or "maximum limit of 10 restarts" in err_str.lower():
            logger.warning(
                f"AE-2624 restart limit reached for {execution_id}. "
                f"Automatically falling back to resubmit_execution."
            )
            try:
                resubmit_resp = resubmit_execution(
                    execution_id=execution_id,
                    from_failure_point=True,
                    reason=f"{reason} (auto-resubmit: restart limit AE-2624 reached)"
                )
                return {
                    **resubmit_resp,
                    "restart_limit_reached": True,
                    "fallback": "resubmit",
                    "hint": (
                        "The restart limit (10) for this execution was reached. "
                        "The system automatically resubmitted it as a new execution instead."
                    ),
                }
            except Exception as resubmit_err:
                return {
                    "success": False,
                    "error": f"Restart limit reached (AE-2624) and resubmit also failed: {resubmit_err}",
                    "hint": (
                        "The restart limit of 10 has been reached and resubmit also failed. "
                        "Please manually trigger a new execution from the AutomationEdge UI."
                    ),
                    "restart_limit_reached": True,
                }

        logger.error(f"Restart failed for {execution_id}: {e}")
        return {
            "success": False,
            "error": f"Restart failed: {err_str}",
            "hint": (
                "Ensure the execution is in a failed state. "
                "For terminal states, try 'resubmit_execution' instead."
            ),
        }


def resubmit_execution(execution_id: str,
                       from_failure_point: bool = True,
                       reason: str = "Resubmitted by support agent") -> dict:
    """Resubmit a failed execution as a NEW run."""
    client = get_ae_client()
    status_resp = None
    try:
        if hasattr(client, "get_execution_status"):
            status_resp = client.get_execution_status(execution_id)
    except Exception as exc:
        logger.warning("Could not resolve execution context for %s before resubmit: %s", execution_id, exc)
        status_resp = None

    status_guard = _guard_failed_execution_only(
        client,
        execution_id,
        "resubmit",
        status_resp=status_resp,
    )
    if status_guard:
        return status_guard

    workflow_name = _extract_workflow_name_from_execution_status(status_resp) or "Unknown"
    agent_guard = _guard_assigned_agents_running(
        client,
        workflow_name,
        execution_id,
        "resubmit",
        status_resp=status_resp,
    )
    if agent_guard:
        return agent_guard

    try:
        resp = client.resubmit_request(
            execution_id, reason=reason, from_failure_point=from_failure_point
        )
        mode = "from failure point" if from_failure_point else "from start"
        new_request_id = _extract_new_execution_ref(resp)
        message = resp.get("message")
        if not message:
            if new_request_id and new_request_id != str(execution_id):
                message = (
                    f"Execution `{execution_id}` has been resubmitted ({mode}) as a new run. "
                    f"New Request ID: `{new_request_id}`."
                )
            else:
                message = f"Request {execution_id} has been resubmitted ({mode})"
        request_ref = str(new_request_id or execution_id or "").strip()
        status_context = _resolve_execution_status_context(
            client,
            request_ref,
            workflow_name=workflow_name,
            poll_interval_sec=2,
            max_attempts=15,
        )
        final_status = status_context.get("status") or ""
        poll_raw = status_context.get("raw") or {}
        detail_msg = status_context.get("detail_msg") or ""

        result = {
            "success": True,
            "message": detail_msg or message,
            "execution_id": request_ref,
            "request_id": request_ref,
            "source_execution_id": execution_id,
            "original_execution_id": execution_id,
            "new_execution_id": new_request_id or "",
            "workflow_name": workflow_name,
            "from_failure_point": from_failure_point,
            "raw": poll_raw or resp,
        }
        if final_status:
            result["status"] = final_status
        if detail_msg:
            result["workflow_response_message"] = detail_msg
        return result
    except Exception as e:
        logger.error(f"Resubmit failed for {execution_id}: {e}")
        return {
            "success": False,
            "error": f"Resubmit failed: {str(e)}"
        }


def trigger_workflow(
    workflow_name: str,
    parameters: dict = None,
    user_id: str = "",
    org_code: str = "",
) -> dict:
    """
    Trigger a new execution of a bot (workflow) with required parameters.

    Improvements over v1
    --------------------
    1. File-param guard fires FIRST — user never gets asked for text params they
       can't supply if the workflow also needs a file upload.
    2. Fuzzy/alias name resolution with a helpful suggestion when the name is unknown.
    3. ALL missing parameters are collected in a single, clearly formatted question
       (type hints + examples) instead of one-at-a-time prompts.
    4. Clean card-style approval message (no raw dict dump).
    5. Every status code maps to a distinct, human-readable message.
    6. Agent-status pre-check — ensures at least one assigned automation agent is
       active before proceeding with parameter collection.
    """
    parameters = parameters or {}
    import re
    # Defensively strip "(ID: 1234)" suffix if present before resolution
    workflow_name = re.sub(r"\s*\(ID:\s*\d+\)\s*$", "", str(workflow_name or ""), flags=re.IGNORECASE).strip()
    client = get_ae_client()

    # ── IMPROVEMENT 2: Smarter workflow name resolution ───────────────────────
    resolved_name = _resolve_cached_workflow_name_for_user(
        client,
        workflow_name,
        user_id=user_id,
        org_code=org_code,
    )

    if not resolved_name:
        # Build a helpful list of workflows the user CAN trigger
        available = []
        if user_id:
            available = get_user_accessible_workflow_names(
                user_id, org_code, require_execute=True, limit=15,
            )
        if not available:
            # Fallback: try fuzzy match from all workflows
            try:
                all_workflows = client.list_workflow_names()
                wf_lower = workflow_name.lower().replace(" ", "_")
                available = [
                    w for w in all_workflows
                    if wf_lower in w.lower() or w.lower() in wf_lower
                ][:10]
            except Exception:
                pass

        if available:
            wf_list = "\n".join(f"  • `{n}`" for n in available)
            msg = _build_trigger_access_message(workflow_name, available)
        else:
            msg = _build_trigger_access_message(workflow_name)
        return {
            "success": False,
            "needs_user_input": True,
            "question": msg,
            "workflow_name": workflow_name,
        }

    resolved_org = str(org_code or default_org_code()).strip()
    workflow_id, _ = _get_cached_workflow_info_for_user(
        client,
        resolved_name,
        user_id=user_id,
        org_code=resolved_org,
    )
    if bool(user_id) or is_execute_enforced():
        if not user_id or not workflow_id or not can_execute_workflow(user_id, workflow_id, resolved_org):
            logger.warning(
                "workflow execution denied for workflow=%s user_id=%r org_code=%r",
                resolved_name,
                user_id,
                resolved_org,
            )
            available = get_user_accessible_workflow_names(
                user_id, resolved_org, require_execute=True, limit=15,
            )
            if available:
                wf_list = "\n".join(f"  • `{n}`" for n in available)
                deny_msg = _build_trigger_access_message(resolved_name, available)
            else:
                deny_msg = _build_trigger_access_message(resolved_name)
            return {
                "success": False,
                "needs_user_input": True,
                "question": deny_msg,
                "workflow_name": resolved_name,
            }

    # Protected-workflow guard (unchanged)
    if resolved_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
        return {
            "success": False,
            "error": (
                f"**{resolved_name}** is a protected workflow and cannot be "
                "triggered automatically. Please request a manual trigger from the operations team."
            ),
        }

    # ── IMPROVEMENT 5: File-param guard fires BEFORE param collection ─────────
    # AE-77: Check for "File" type parameters — file upload not supported in agentic chat.
    schema = client.get_cached_workflow_parameters(resolved_name)
    file_params = [
        p.get("name") for p in schema
        if str(p.get("type") or p.get("uiControlType") or "").strip().lower()
        in {"file", "attachment", "upload"}
    ]
    if file_params:
        logger.info(f"Workflow '{resolved_name}' requires file upload — rejecting agentic trigger.")
        param_list = ", ".join(f"`{p}`" for p in file_params)
        return {
            "success": False,
            "error": (
                f"I've identified that **{resolved_name}** requires a **file upload** "
                f"for the following parameter(s): {param_list}.\n\n"
                "Since file uploading is not supported via chat, please log in to the "
                "**AutomationEdge (AE) portal** to trigger this bot manually.\n\n"
                "Thank you for your patience!"
            ),
            "reason": f"Workflow requires file upload for: {', '.join(file_params)}",
            "workflow_name": resolved_name,
        }

    # ── IMPROVEMENT 6: Agent status check ─────────────────────────────────────
    # Check if at least one agent assigned to this workflow is RUNNING/CONNECTED.
    # Also capture the running agent so we can tell AE which one to use.
    _picked_agent_id = ""
    _picked_agent_name = ""
    try:
        assigned_agents = _get_assigned_agents_for_workflow(client, resolved_name)
        if assigned_agents:
            running_agents = [
                a for a in assigned_agents
                if str(a.get("agentState", "")).upper() in {"RUNNING", "CONNECTED", "ACTIVE"}
            ]
            if not running_agents:
                agent_names = _assigned_agent_summary(assigned_agents) or "No agents assigned"
                return {
                    "success": False,
                    "error": (
                        f"**{resolved_name}** cannot be triggered because all assigned agents are currently offline or stopped ({agent_names}).\n\n"
                        "Please start at least one assigned agent in the AutomationEdge portal before proceeding."
                    ),
                    "workflow_name": resolved_name,
                    "agent_name": _primary_assigned_agent_name(assigned_agents),
                    "assigned_agents": assigned_agents,
                }
            # Pick the first running agent to pass to execute_workflow
            picked = running_agents[0]
            _picked_agent_id = str(picked.get("id") or picked.get("agentId") or picked.get("uuid") or "")
            _picked_agent_name = str(picked.get("agentName") or picked.get("name") or "")
            logger.info(
                "Pre-trigger: picked running agent %s (id=%s) for workflow %s (out of %d assigned, %d running)",
                _picked_agent_name, _picked_agent_id, resolved_name,
                len(assigned_agents), len(running_agents),
            )
    except Exception as exc:
        logger.warning(f"Pre-trigger agent check failed for {resolved_name}: {exc}")

    # ── IMPROVEMENT 6b: Pre-trigger health gate ───────────────────────────────
    # If the most recent execution of this workflow FAILED due to Life Asia or
    # TEBT issues, run BOTH health check workflows before allowing the trigger.
    _health_gate_summary = None
    try:
        _health_gate_summary = _pre_trigger_health_gate(client, resolved_name, resolved_org)
    except _HealthGateBlockError as hg_err:
        return {
            "success": False,
            "blocked_reason": "health_check_failed",
            "message": str(hg_err),
            "workflow_name": resolved_name,
        }
    except Exception as exc:
        logger.warning("Pre-trigger health gate failed for %s: %s", resolved_name, exc)
        # Non-blocking: if the gate itself errors, let the trigger proceed.

    # ── IMPROVEMENT 7: In-progress guard (Concurrent execution check) ─────────
    # Before triggering, check if an instance of THIS workflow is already running.
    # Block the trigger if one is found to prevent duplicate executions.
    try:
        running = client.get_running_instances(resolved_name)
        if running:
            inst = running[0]
            # Extract execution ID from various possible T4 fields
            exec_id = (
                inst.get("id") 
                or inst.get("automationRequestId") 
                or inst.get("requestId") 
                or inst.get("executionId") 
                or "?"
            )
            # Friendly display name
            friendly = resolved_name.replace("_", " ").replace("-", " ").title()
            logger.info("Trigger blocked for %s: instance %s is already running", resolved_name, exec_id)
            return {
                "success": False,
                "blocked_reason": "concurrent_execution",
                "message": (
                    f"**{friendly}** is already running (Execution ID: `{exec_id}`).\n\n"
                    f"Please wait for the current execution to complete before triggering it again. "
                    f"I'll help you monitor the status if needed!"
                ),
                "workflow_name": resolved_name,
                "running_instances": running[:3], # return a few for context
            }
    except Exception as exc:
        logger.warning("In-progress check failed for %s: %s", resolved_name, exc)
        # Non-blocking: if the check fails (e.g. API error), we proceed with the trigger
        # to avoid blocking users due to monitoring tool failures.

    # ── IMPROVEMENT 1: Batch parameter collection ─────────────────────────────
    required = client.get_required_parameters(resolved_name)       # list[str]
    param_schema_map = {p.get("name"): p for p in schema}         # name → schema entry

    missing = [p for p in required if not parameters.get(p)]

    if missing:
        param_lines = []
        for p in missing:
            meta = param_schema_map.get(p, {})
            p_type    = meta.get("type") or meta.get("uiControlType") or "text"
            p_example = meta.get("example") or meta.get("defaultValue") or ""
            example_str = f" _(e.g. `{p_example}`)_" if p_example else ""
            param_lines.append(f"  • **{p}** `[{p_type}]`{example_str}")

        friendly_name = resolved_name.replace("_", " ").replace("-", " ").title()
        if len(missing) == 1:
            only = missing[0]
            only_meta = param_schema_map.get(only, {})
            only_desc = str(only_meta.get("description") or only_meta.get("displayName") or "").strip()
            question = f"I'm ready to trigger **{friendly_name}**. What should I use for **{only}**?"
            if only_desc:
                question += f"\n\nExpected format: {only_desc}."
            question += "\n\nOnce you share it, I'll kick it off right away."
        else:
            question = (
                f"I'm ready to trigger **{friendly_name}**. "
                "Please share these details:\n\n"
                + "\n".join(param_lines)
                + "\n\nShare all of them together and I'll kick it off right away."
            )
        return {
            "success": False,
            "needs_user_input": True,
            "question": question,
            "tool_name": "trigger_workflow",
            "workflow_name": resolved_name,
            "missing_params": missing,
        }

    # ── IMPROVEMENT 4: Pre-build clean approval message for the gate ──────────
    # ApprovalGate middleware reads `approval_message` from the return dict.
    # We inject a formatted preview here so whatever the gate shows is human-friendly.
    approval_preview = _build_approval_message(
        workflow_name=resolved_name,
        parameters=parameters,
        risk="medium_risk",
    )

    # ── Execute ───────────────────────────────────────────────────────────────
    try:
        if not workflow_id:
            workflow_id = resolved_name

        raw = client.execute_workflow(
            workflow_name=resolved_name,
            workflow_id=workflow_id,
            params=parameters,
            org_code=resolved_org,
            user_id=user_id,
            source="ops-agent-remediation",
            agent_id=_picked_agent_id,
            agent_name=_picked_agent_name,
        )

        # Strict validation: T4 usually returns a Request ID
        req_id = (
            raw.get("automationRequestId")
            or raw.get("requestId")
            or raw.get("id")
        )

        # If we got a 200/201 but the body says success=False or has no ID, it's a failure
        if not req_id and not raw.get("success", True):
            error_msg = (
                raw.get("errorMessage")
                or raw.get("errorDetails")
                or raw.get("error")
                or raw.get("message")
                or raw.get("raw")
                or "T4 returned failure without details."
            )
            return {
                "success": False,
                "error": f"Trigger failed: {error_msg}",
                "raw": raw,
            }

        if not req_id:
            logger.warning(f"T4 trigger for '{resolved_name}' succeeded but returned no Request ID.")

        # ── Poll execution status ─────────────────────────────────────────────
        final_status = raw.get("status") or raw.get("state") or "QUEUED"
        poll_raw = {}
        poll_result = {}  # always defined so later references are safe

        detail_msg = ""
        if req_id:
            status_context = _resolve_execution_status_context(
                client,
                str(req_id),
                workflow_name=resolved_name,
                poll_interval_sec=2,
                max_attempts=15,
            )
            poll_result = status_context.get("poll_result") or {}
            final_status = status_context.get("status") or final_status
            poll_raw = status_context.get("raw") or {}
            detail_msg = status_context.get("detail_msg") or ""

        new_diag = poll_raw.get("newExecutionDiagnosis") if isinstance(poll_raw, dict) and isinstance(poll_raw.get("newExecutionDiagnosis"), dict) else {}
        if not detail_msg and new_diag.get("summary"):
            detail_msg = str(new_diag.get("summary"))

        # ── IMPROVEMENT 3: Terminal failure path ──────────────────────────────
        status_upper = str(final_status or "").upper()
        if status_upper in {"FAILURE", "ERROR"}:
            error_msg = (
                poll_raw.get("errorMessage")
                or poll_raw.get("errorDetails")
                or detail_msg
                or ""
            )
            return {
                "success": False,
                "execution_id": req_id,
                "workflow_name": resolved_name,
                "status": final_status,
                "error": _friendly_status_message(
                    status=final_status,
                    workflow_name=resolved_name,
                    req_id=req_id,
                    detail_msg=error_msg,
                ),
                "request_id": req_id,
                "raw": poll_raw or raw,
            }

        # ── IMPROVEMENT 3: Non-terminal / pending path ────────────────────────
        if status_upper == "WAITING_OTHER_PROCESS":
            accepted_prefix = (
                f"Request ID `{req_id}` is already triggered and currently in **New** status."
            )
            if detail_msg:
                pending_msg = f"{accepted_prefix} {detail_msg}"
            else:
                pending_msg = _friendly_status_message(
                    status=final_status,
                    workflow_name=resolved_name,
                    req_id=req_id,
                    detail_msg=detail_msg,
                )
            return {
                "success": True,
                "execution_id": req_id,
                "workflow_name": resolved_name,
                "status": "New",
                "message": pending_msg,
                "request_id": req_id,
                "raw": poll_raw or raw,
                "diagnosis": new_diag.get("reason") or "other_process_running",
                "other_execution_id": new_diag.get("other_execution_id") or "",
                "other_workflow_name": new_diag.get("other_workflow_name") or "",
            }

        if status_upper == "NO_AGENT":
            assigned_agents = list(new_diag.get("assigned_agents") or [])
            if not assigned_agents:
                assigned_agents = _get_assigned_agents_for_workflow(client, resolved_name)
            pending_msg = _friendly_status_message(
                status=final_status,
                workflow_name=resolved_name,
                req_id=req_id,
                detail_msg=detail_msg,
                assigned_agents=assigned_agents,
            )
            return {
                "success": False,
                "execution_id": req_id,
                "workflow_name": resolved_name,
                "status": final_status,
                "error": pending_msg,
                "message": pending_msg,
                "request_id": req_id,
                "raw": poll_raw or raw,
                "agent_name": _primary_assigned_agent_name(assigned_agents),
                "assigned_agents": assigned_agents,
                "diagnosis": new_diag.get("reason") or "agent_unavailable",
                "agent_status": "UNAVAILABLE",
            }

        healthy_agent = None
        if status_upper != "COMPLETE":
            in_progress_hint = poll_result.get("in_progress_hint") if poll_result else None
            if status_upper == "IN_PROGRESS" and in_progress_hint:
                pending_msg = in_progress_hint
            else:
                try:
                    agent_data = client.check_agent_status()
                    healthy_agent = next(
                        (
                            a for a in agent_data
                            if str(a.get("agentState", "")).upper() in {"CONNECTED", "RUNNING", "ACTIVE"}
                        ),
                        None,
                    )
                except Exception as agent_exc:
                    logger.warning("Agent status check failed: %s", agent_exc)

                pending_msg = _friendly_status_message(
                    status=final_status,
                    workflow_name=resolved_name,
                    req_id=req_id,
                    detail_msg=detail_msg,
                    healthy_agent=healthy_agent,
                )
        else:
            pending_msg = _friendly_status_message(
                status=final_status,
                workflow_name=resolved_name,
                req_id=req_id,
                detail_msg=detail_msg,
            )

        result = {
            "success": True,
            "execution_id": req_id,
            "workflow_name": resolved_name,
            "status": final_status or "QUEUED",
            "message": pending_msg,
            "approval_message": approval_preview,   # clean gate message
            "request_id": req_id,
            "raw": poll_raw or raw,
            "agent_status": healthy_agent.get("agentState") if healthy_agent else "UNAVAILABLE",
        }
        if detail_msg:
            result["workflow_response_message"] = detail_msg
        # Attach pre-trigger health gate summary (if health checks were run and passed)
        if _health_gate_summary and isinstance(_health_gate_summary, dict):
            result["health_gate"] = _health_gate_summary
            # Prepend health info to the user message
            hg_msg = _health_gate_summary.get("health_summary", "")
            if hg_msg:
                result["message"] = f"{hg_msg}\n\n{pending_msg}"
        return result

    except Exception as e:
        logger.error(f"Failed to trigger workflow '{resolved_name}': {e}")
        return {
            "success": False,
            "error": (
                f"An unexpected error occurred while triggering **{resolved_name}**: {e}\n\n"
                "Please try again or contact your administrator."
            ),
        }


def requeue_item(queue_name: str, item_id: str) -> dict:
    resp = get_ae_client().post(
        f"/api/v1/queues/{queue_name}/items/{item_id}/requeue"
    )
    return {
        "success": True,
        "queue_name": queue_name,
        "item_id": item_id,
        "new_status": resp.get("status", "queued"),
    }


def bulk_retry_failures(workflow_name: str = "", hours: int = 24,
                        max_retries: int = None) -> dict:
    max_ops = max_retries or CONFIG.get("MAX_BULK_OPERATIONS", 10)
    resp = get_ae_client().post(
        "/api/v1/executions/bulk-retry",
        payload={
            "workflow_name": workflow_name,
            "hours": hours,
            "max_retries": max_ops,
        },
    )
    return {
        "success": True,
        "retried_count": resp.get("retriedCount", 0),
        "skipped_count": resp.get("skippedCount", 0),
        "errors": resp.get("errors", []),
    }


def disable_workflow(workflow_name: str, reason: str = "") -> dict:
    resp = get_ae_client().post(
        f"/api/v1/workflows/{workflow_name}/disable",
        payload={"reason": reason},
    )
    return {
        "success": True,
        "workflow_name": workflow_name,
        "status": resp.get("status"),
        "reason": reason,
    }


def disable_schedule(schedule_id: str, reason: str = "") -> dict:
    """Disable or pause a schedule. Use when asked 'How can I pause or disable this schedule?'."""
    resp = get_ae_client().disable_schedule(schedule_id, reason=reason)
    return {
        "success": True,
        "schedule_id": schedule_id,
        "message": resp.get("message") or f"Schedule {schedule_id} disabled successfully",
        "raw": resp
    }


def enable_schedule(schedule_id: str, reason: str = "") -> dict:
    """Enable or resume a schedule. Use when asked 'How do I resume or enable this schedule?'."""
    resp = get_ae_client().enable_schedule(schedule_id, reason=reason)
    return {
        "success": True,
        "schedule_id": schedule_id,
        "message": resp.get("message") or f"Schedule {schedule_id} enabled successfully",
        "raw": resp
    }


# ─────────────────────────────────────────────────────────────────────────────
# Register all remediation tools
# ─────────────────────────────────────────────────────────────────────────────

tool_registry.register(
    ToolDefinition(
        name="restart_execution",
        description=(
            "Restart a failed bot (workflow) execution or request. "
            "Pass the execution_id (request id) to trigger the restart. "
            "Use this only when the execution is in a failed state."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "Failed execution ID (request id)",
            },
            "workflow_name": {
                "type": "string",
                "description": "Workflow name (optional if execution_id is known)",
            },
            "from_checkpoint": {
                "type": "boolean",
                "description": "Resume from checkpoint (default true)",
            },
            "reason": {
                "type": "string",
                "description": "Reason for restart",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Simulate restart without executing",
            },
        },
        required_params=["execution_id"],
    ),
    restart_execution,
)

tool_registry.register(
    ToolDefinition(
        name="resubmit_execution",
        description=(
            "Resubmit a failed bot (workflow) execution as a NEW run. "
            "DIFFERENT from restart_execution: restart resumes the SAME execution; "
            "resubmit creates a NEW execution. "
            "Use when a failed execution needs a fresh run. "
            "Use from_failure_point=True to retry from where it failed, "
            "or from_failure_point=False to start fresh from the beginning."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The failed execution ID (request id) to resubmit",
            },
            "from_failure_point": {
                "type": "boolean",
                "description": "If true, resubmit from the last failure step. If false, resubmit from start. Default: true",
            },
            "reason": {
                "type": "string",
                "description": "Reason for resubmitting",
            },
        },
        required_params=["execution_id"],
        use_when=(
            "User explicitly asks to 'resubmit', 'run again from scratch', or when "
            "restart fails and a fresh execution is needed."
        ),
        avoid_when=(
            "User says 'restart' — use restart_execution instead."
        ),
    ),
    resubmit_execution,
)

tool_registry.register(
    ToolDefinition(
        name="trigger_workflow",
        description=(
            "Trigger a new execution of a bot (workflow) with required parameters. "
            "Collects all missing parameters in one step, resolves fuzzy workflow names, "
            "detects file-upload requirements early, and returns clean human-readable "
            "status messages."
        ),
        category="remediation",
        tier="medium_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "The name (or partial/alias) of the bot (workflow) to trigger",
            },
            "parameters": {
                "type": "object",
                "description": "Workflow input parameters as a dictionary",
            },
        },
        required_params=["workflow_name", "parameters"],
        always_available=True,
        use_when=(
            "You know the target workflow and need to execute it using the "
            "workflow name plus collected runtime parameters."
        ),
        avoid_when=(
            "A safer read-only diagnostic tool can answer the question without "
            "starting automation."
        ),
        input_examples=[
            {
                "workflow_name": "Claims_Processing_Daily",
                "parameters": {"businessDate": "2026-03-07", "region": "west"},
            }
        ],
    ),
    trigger_workflow,
)

tool_registry.register(
    ToolDefinition(
        name="requeue_item",
        description="Requeue a failed queue item for reprocessing.",
        category="remediation",
        tier="low_risk",
        parameters={
            "queue_name": {
                "type": "string",
                "description": "Queue name",
            },
            "item_id": {
                "type": "string",
                "description": "Item ID to requeue",
            },
        },
        required_params=["queue_name", "item_id"],
    ),
    requeue_item,
)

tool_registry.register(
    ToolDefinition(
        name="bulk_retry_failures",
        description=(
            "Retry all failed bot (workflow) executions within a time window. "
            "Use with caution — high impact operation."
        ),
        category="remediation",
        tier="high_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Filter by bot/workflow name (empty for all)",
            },
            "hours": {
                "type": "integer",
                "description": "Time window in hours (default 24)",
            },
            "max_retries": {
                "type": "integer",
                "description": "Max retries to attempt",
            },
        },
        required_params=[],
    ),
    bulk_retry_failures,
)

tool_registry.register(
    ToolDefinition(
        name="disable_workflow",
        description=(
            "Disable a bot (workflow) to prevent future scheduled runs. "
            "Use when a bot is causing cascading failures."
        ),
        category="remediation",
        tier="high_risk",
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "The bot (workflow) to disable",
            },
            "reason": {
                "type": "string",
                "description": "Reason for disabling",
            },
        },
        required_params=["workflow_name"],
    ),
    disable_workflow,
)

# tool_registry.register(
#     ToolDefinition(
#         name="ae.schedule.disable",
#         description="Disable or pause a schedule. Use when asked 'How can I pause or disable this schedule?'. Needs a schedule_id.",
#         category="remediation",
#         tier="high_risk",
#         parameters={
#             "schedule_id": {"type": "string", "description": "Schedule ID to disable"},
#             "reason": {"type": "string", "description": "Reason for disabling", "optional": True},
#         },
#         required_params=["schedule_id"],
#     ),
#     disable_schedule,
# )

# tool_registry.register(
#     ToolDefinition(
#         name="ae.schedule.enable",
#         description="Enable or resume a schedule. Use when asked 'How do I resume or enable this schedule?'. Needs a schedule_id.",
#         category="remediation",
#         tier="high_risk",
#         parameters={
#             "schedule_id": {"type": "string", "description": "Schedule ID to enable"},
#             "reason": {"type": "string", "description": "Reason for enabling", "optional": True},
#         },
#         required_params=["schedule_id"],
#     ),
#     enable_schedule,
# )
