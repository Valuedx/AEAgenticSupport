"""
Progress reporting for long-running agent operations.

Generates user-friendly status messages as the agent investigates,
and delivers them via channel-specific callbacks (webchat SSE, Teams
proactive messages, etc.).

Usage:
    cb = ProgressCallback(on_progress=my_send_fn, user_role="business")
    orchestrator.handle_message(msg, state, on_progress=cb)

The orchestrator calls ``cb.on_tool_start(tool_name, tool_args)`` and
``cb.on_tool_done(tool_name, result)`` at each step.  The callback
throttles messages (min 3 s apart) and maps tool names to
human-readable status text.
"""
from __future__ import annotations

import time
import logging
from typing import Callable, Optional
from state.app_config import get_progress_min_interval

logger = logging.getLogger("ops_agent.progress")

MIN_INTERVAL_SECONDS = 3.0

# ── Tool name → (business message, technical message) ──
_TOOL_MESSAGES: dict[str, tuple[str, str]] = {
    "check_workflow_status": (
        "Checking the latest process status...",
        "Checking the latest workflow status...",
    ),
    "list_recent_failures": (
        "Looking for recent issues...",
        "Listing recent failures...",
    ),
    "get_system_health": (
        "Checking overall system health...",
        "Pulling system health metrics...",
    ),
    "get_execution_logs": (
        "Reviewing the latest activity logs...",
        "Pulling the latest execution logs...",
    ),
    "get_execution_history": (
        "Checking run history...",
        "Retrieving execution history...",
    ),
    "check_input_file": (
        "Verifying input files...",
        "Checking input file presence and format...",
    ),
    "check_output_file": (
        "Verifying output files...",
        "Checking output file...",
    ),
    "get_workflow_dependencies": (
        "Checking related processes...",
        "Tracing workflow dependencies...",
    ),
    "get_workflow_config": (
        "Reviewing the process setup...",
        "Fetching workflow configuration...",
    ),
    "get_schedule_info": (
        "Checking schedule...",
        "Pulling schedule details...",
    ),
    "get_queue_status": (
        "Checking queue status...",
        "Checking queue depth and health...",
    ),
    "get_agent_status": (
        "Checking agent availability...",
        "Checking AutomationEdge agent status...",
    ),
    "check_agent_resources": (
        "Checking system resources...",
        "Checking agent CPU/memory...",
    ),
    "restart_execution": (
        "Restarting the process...",
        "Restarting execution from checkpoint...",
    ),
    "trigger_workflow": (
        "Triggering the process...",
        "Triggering workflow execution...",
    ),
    "requeue_item": (
        "Re-queuing the item...",
        "Requeuing failed item...",
    ),
    "bulk_retry_failures": (
        "Retrying failed items...",
        "Running bulk retry...",
    ),
    "disable_workflow": (
        "Disabling the workflow...",
        "Disabling workflow to prevent further failures...",
    ),
    "send_notification": (
        "Sending notification...",
        "Sending team notification...",
    ),
    "create_incident_ticket": (
        "Creating a support ticket...",
        "Creating incident ticket...",
    ),
    "create_support_ticket": (
        "Creating a support ticket...",
        "Creating support ticket...",
    ),
    "discover_tools": (
        "Looking for the best way to handle this...",
        "Searching the tool catalog for the right next step...",
    ),
    "call_ae_api": (
        "Checking AutomationEdge directly...",
        "Querying AutomationEdge directly...",
    ),
    "query_database": (
        "Looking up records...",
        "Running database query...",
    ),
    "search_knowledge_base": (
        "Searching knowledge base...",
        "Searching knowledge base...",
    ),
}

_PHASE_MESSAGES: dict[str, tuple[str, str]] = {
    "investigating": (
        "I'm looking into this now...",
        "Investigation started...",
    ),
    "analyzing": (
        "I'm reviewing what I found...",
        "Reviewing the findings...",
    ),
    "found_error": (
        "Found an issue — analyzing the cause...",
        "Error detected — analyzing root cause...",
    ),
    "multiple_failures": (
        "Multiple issues detected — tracing the root cause...",
        "Multiple failures — tracing upstream dependency chain...",
    ),
    "preparing_fix": (
        "I found the next action. Preparing it for your approval...",
        "Remediation identified. Preparing the approval request...",
    ),
    "executing_fix": (
        "Running the approved action now...",
        "Executing approved remediation...",
    ),
    "generating_rca": (
        "Preparing a summary of what happened...",
        "Generating root cause analysis...",
    ),
    "almost_done": (
        "Almost done — putting together my response...",
        "Finalizing response...",
    ),
}
_FRIENDLY_PHASE_MESSAGES: dict[str, tuple[str, str]] = {
    "investigating": (
        "I'm looking into this now...",
        "Investigation started...",
    ),
    "analyzing": (
        "I'm reviewing what I found...",
        "Reviewing the findings...",
    ),
    "found_error": (
        "I found an issue and I'm checking the cause...",
        "I found an issue and I'm checking the root cause...",
    ),
    "multiple_failures": (
        "I found multiple issues and I'm tracing the root cause...",
        "Multiple failures detected. Tracing the upstream dependency chain...",
    ),
    "preparing_fix": (
        "I found the next action. Preparing it for your approval...",
        "Remediation identified. Preparing the approval request...",
    ),
    "executing_fix": (
        "Running the approved action now...",
        "Executing approved remediation...",
    ),
    "generating_rca": (
        "Preparing a summary of what happened...",
        "Generating root cause analysis...",
    ),
    "almost_done": (
        "Almost done. I'm putting together the response...",
        "Almost done. Finalizing the response...",
    ),
}


class ProgressCallback:
    """Generates and delivers progress messages during agent execution."""

    def __init__(
        self,
        send_fn: Optional[Callable[[str], None]] = None,
        user_role: str = "technical",
        min_interval: float | None = None,
    ):
        self._send = send_fn
        self._role = user_role
        self._min_interval = (
            float(min_interval)
            if min_interval is not None
            else float(get_progress_min_interval())
        )
        self._last_sent: float = 0.0
        self._sent_count: int = 0
        self._tool_history: list[str] = []

    @property
    def is_active(self) -> bool:
        return self._send is not None

    def on_phase(self, phase: str) -> None:
        """Emit a progress message for a high-level phase change."""
        msgs = _FRIENDLY_PHASE_MESSAGES.get(phase) or _PHASE_MESSAGES.get(phase)
        if msgs:
            text = msgs[0] if self._role == "business" else msgs[1]
            self._emit(text)

    def on_tool_start(self, tool_name: str, tool_args: dict) -> None:
        """Emit a progress message when a tool call begins."""
        self._tool_history.append(tool_name)

        if tool_name in ("discover_tools",):
            return

        msgs = _TOOL_MESSAGES.get(tool_name)
        if msgs:
            text = msgs[0] if self._role == "business" else msgs[1]
        else:
            text = (
                "Working on it..."
                if self._role == "business"
                else f"Calling {tool_name}..."
            )

        wf = tool_args.get("workflow_name", "")
        if wf and self._role == "technical":
            text = text.rstrip(".") + f" ({wf})..."

        self._emit(text)

    def on_tool_done(self, tool_name: str, success: bool,
                     result_hint: str = "") -> None:
        """Optionally emit after a tool completes if there's a notable finding."""
        if not success and result_hint:
            text = (
                f"I found an issue: {result_hint[:100]}"
                if self._role == "business"
                else f"I found an issue while running {tool_name}: {result_hint[:120]}"
            )
            self._emit(text, force=True)
            return

        if not success and result_hint:
            text = (
                f"Found an issue — {result_hint[:100]}"
                if self._role == "business"
                else f"Error from {tool_name}: {result_hint[:120]}"
            )
            self._emit(text, force=True)

    def on_iteration(self, iteration: int, max_iterations: int) -> None:
        """Emit a heartbeat on long investigations."""
        if iteration > 0 and iteration % 4 == 0:
            text = (
                "I'm still investigating this..."
                if self._role == "business"
                else f"Investigation step {iteration}/{max_iterations} in progress..."
            )
            self._emit(text)

    def _emit(self, text: str, force: bool = False) -> None:
        if not self._send:
            return
        now = time.time()
        if not force and (now - self._last_sent) < self._min_interval:
            return
        try:
            self._send(text)
            self._last_sent = now
            self._sent_count += 1
        except Exception as e:
            logger.warning(f"Progress send failed: {e}")


def create_noop_progress() -> ProgressCallback:
    """A silent callback for channels that don't support progress."""
    return ProgressCallback(send_fn=None)
