"""
Scheduled agent — proactive health checks and autonomous monitoring.

Runs periodic tasks in background threads:
* Health check polls — check system/workflow health at regular intervals
* Event-driven triggers — react to webhook payloads from AE
* Scheduled report generation (daily/weekly ops summaries)

Uses a lightweight scheduler backed by threading.Timer to avoid
external dependencies (Celery, APScheduler) for the core product.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional

from state.app_config import get_runtime_value
from state.scheduler_store import get_scheduler_store

logger = logging.getLogger("ops_agent.scheduler")


# ── Schedule types ───────────────────────────────────────────────────

class ScheduleType(Enum):
    INTERVAL = "interval"       # Run every N seconds
    CRON_LIKE = "cron_like"     # Simple cron (hour/minute)
    ONE_SHOT = "one_shot"       # Run once after delay


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledTask:
    """Definition of a scheduled/proactive task."""
    task_id: str = field(
        default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}"
    )
    name: str = ""
    description: str = ""
    schedule_type: ScheduleType = ScheduleType.INTERVAL
    interval_seconds: int = 300  # Default: 5 minutes
    cron_hour: int = -1          # -1 = every hour
    cron_minute: int = 0
    enabled: bool = True
    handler_name: str = ""       # Name of the handler to invoke
    handler_args: dict = field(default_factory=dict)
    last_run: str = ""
    last_result: str = ""
    last_status: TaskStatus = TaskStatus.PENDING
    run_count: int = 0
    failure_count: int = 0
    is_system: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "schedule_type": self.schedule_type.value,
            "interval_seconds": self.interval_seconds,
            "cron_hour": self.cron_hour,
            "cron_minute": self.cron_minute,
            "enabled": self.enabled,
            "handler_name": self.handler_name,
            "handler_args": dict(self.handler_args or {}),
            "last_run": self.last_run,
            "last_result": self.last_result,
            "last_status": self.last_status.value,
            "run_count": self.run_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at,
            "is_system": self.is_system,
            "managed_by": "system" if self.is_system else "custom",
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScheduledTask":
        return cls(
            task_id=str(payload.get("task_id", "")).strip() or f"task-{uuid.uuid4().hex[:8]}",
            name=str(payload.get("name", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            schedule_type=ScheduleType(str(payload.get("schedule_type", "interval"))),
            interval_seconds=max(1, int(payload.get("interval_seconds", 300) or 300)),
            cron_hour=int(payload.get("cron_hour", -1) or -1),
            cron_minute=int(payload.get("cron_minute", 0) or 0),
            enabled=bool(payload.get("enabled", True)),
            handler_name=str(payload.get("handler_name", "")).strip(),
            handler_args=dict(payload.get("handler_args", {}) or {}),
            last_run=str(payload.get("last_run", "")).strip(),
            last_result=str(payload.get("last_result", "")).strip(),
            last_status=TaskStatus(str(payload.get("last_status", "pending"))),
            run_count=int(payload.get("run_count", 0) or 0),
            failure_count=int(payload.get("failure_count", 0) or 0),
            is_system=bool(payload.get("is_system", False)),
            created_at=str(payload.get("created_at", "")).strip() or datetime.now().isoformat(),
        )


@dataclass
class TaskResult:
    """Result from executing a scheduled task."""
    success: bool
    message: str
    data: dict = field(default_factory=dict)
    alerts: list[dict] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )


# ── Built-in health check handlers ──────────────────────────────────

def health_check_handler(**kwargs) -> TaskResult:
    """
    Proactive health check that polls AE for system status.
    Returns alerts if any issues are detected.
    """
    alerts = []
    try:
        from tools.registry import tool_registry

        # Check system health
        result = tool_registry.execute("get_system_health")
        if result.success:
            data = result.data or {}
            status = str(data.get("status", "")).upper()
            if status not in ("HEALTHY", "OK", "UP"):
                alerts.append({
                    "severity": "warning",
                    "message": f"System health status: {status}",
                    "source": "health_check",
                    "timestamp": datetime.now().isoformat(),
                })

        # Check for recent failures
        hours = kwargs.get("hours", 1)
        fail_result = tool_registry.execute(
            "list_recent_failures",
            hours=hours,
        )
        if fail_result.success:
            failures = fail_result.data
            if isinstance(failures, dict):
                fail_count = failures.get("total", 0)
            elif isinstance(failures, list):
                fail_count = len(failures)
            else:
                fail_count = 0

            if fail_count > 0:
                alerts.append({
                    "severity": "error",
                    "message": f"{fail_count} failures in the last {hours}h",
                    "source": "failure_scan",
                    "count": fail_count,
                    "timestamp": datetime.now().isoformat(),
                })

        return TaskResult(
            success=True,
            message=f"Health check complete. {len(alerts)} alert(s).",
            alerts=alerts,
        )
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        return TaskResult(
            success=False,
            message=f"Health check error: {exc}",
        )


def workflow_monitor_handler(**kwargs) -> TaskResult:
    """Monitor specific workflows for failures."""
    workflows = kwargs.get("workflows", [])
    alerts = []
    try:
        from tools.registry import tool_registry

        for wf_name in workflows:
            result = tool_registry.execute(
                "check_workflow_status",
                workflow_name=wf_name,
            )
            if result.success:
                data = result.data or {}
                status = str(data.get("status", "")).upper()
                if status in ("FAILED", "ERROR", "STUCK"):
                    alerts.append({
                        "severity": "error",
                        "message": f"Workflow '{wf_name}' is {status}",
                        "workflow": wf_name,
                        "status": status,
                        "timestamp": datetime.now().isoformat(),
                    })

        return TaskResult(
            success=True,
            message=f"Monitored {len(workflows)} workflow(s). {len(alerts)} alert(s).",
            alerts=alerts,
        )
    except Exception as exc:
        return TaskResult(success=False, message=str(exc))


def daily_summary_handler(**kwargs) -> TaskResult:
    """Generate a daily ops summary report."""
    try:
        from config.llm_client import llm_client
        from tools.registry import tool_registry

        # Gather data
        health = tool_registry.execute("get_system_health")
        failures = tool_registry.execute("list_recent_failures", hours=24)

        health_data = health.data if health.success else {}
        failure_data = failures.data if failures.success else []

        prompt = f"""Generate a concise daily ops summary report.

System Health: {json.dumps(health_data, default=str)[:500]}
Failures (24h): {json.dumps(failure_data, default=str)[:1000]}

Format as a brief report with:
1. Overall Status (one line)
2. Key Metrics (bullet points)
3. Issues Requiring Attention (if any)
4. Recommendation (one line)

Keep it under 300 words."""

        summary = llm_client.chat(
            prompt,
            system="You write concise daily ops reports for IT operations teams.",
        )

        return TaskResult(
            success=True,
            message="Daily summary generated",
            data={"summary": summary},
        )
    except Exception as exc:
        return TaskResult(success=False, message=str(exc))


# ── Handler registry ─────────────────────────────────────────────────

_BUILTIN_HANDLERS: dict[str, Callable[..., TaskResult]] = {
    "health_check": health_check_handler,
    "workflow_monitor": workflow_monitor_handler,
    "daily_summary": daily_summary_handler,
}

_HANDLER_SUMMARIES: dict[str, str] = {
    "health_check": "Checks platform health and recent failures at a regular interval.",
    "workflow_monitor": "Watches a defined list of workflows for failures or stuck states.",
    "daily_summary": "Creates a daily plain-language operations summary for stakeholders.",
    "session_cleanup": "Removes stale conversation state after the configured retention period.",
}


# ── Scheduler engine ────────────────────────────────────────────────

class AgentScheduler:
    """
    Lightweight task scheduler for the proactive agent layer.

    Runs scheduled tasks in background threads. Tasks are registered
    with the scheduler and executed at their configured intervals.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._custom_handlers: dict[str, Callable[..., TaskResult]] = {}
        self._alert_callbacks: list[Callable[[list[dict]], None]] = []
        self._running = False
        self._execution_log: list[dict] = []
        self._max_log_size = 200

    # ── Task management ──────────────────────────────────────────────

    def add_task(self, task: ScheduledTask) -> None:
        with self._lock:
            existing_timer = self._timers.pop(task.task_id, None)
            if existing_timer:
                existing_timer.cancel()
            self._tasks[task.task_id] = task
            logger.info("Scheduled task added: %s (%s)", task.name, task.task_id)
            if self._running and task.enabled:
                self._schedule_next(task)

    def upsert_task(self, task: ScheduledTask) -> None:
        self.add_task(task)

    def remove_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._timers:
                self._timers[task_id].cancel()
                del self._timers[task_id]
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False

    def enable_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.enabled = True
            if self._running:
                self._schedule_next(task)
            return True

    def disable_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.enabled = False
            if task_id in self._timers:
                self._timers[task_id].cancel()
                del self._timers[task_id]
            return True

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_handler_catalog(self) -> list[dict[str, str]]:
        names = sorted(set(_BUILTIN_HANDLERS) | set(self._custom_handlers))
        return [
            {
                "name": name,
                "summary": _HANDLER_SUMMARIES.get(
                    name,
                    "Custom scheduler handler registered at runtime.",
                ),
                "source": "builtin" if name in _BUILTIN_HANDLERS else "custom",
            }
            for name in names
        ]

    # ── Handler registration ─────────────────────────────────────────

    def register_handler(
        self, name: str, handler: Callable[..., TaskResult],
    ) -> None:
        self._custom_handlers[name] = handler

    def _get_handler(self, name: str) -> Optional[Callable[..., TaskResult]]:
        return (
            self._custom_handlers.get(name)
            or _BUILTIN_HANDLERS.get(name)
        )

    # ── Alert callbacks ──────────────────────────────────────────────

    def on_alerts(self, callback: Callable[[list[dict]], None]) -> None:
        """Register a callback to be invoked when alerts are generated."""
        self._alert_callbacks.append(callback)

    def _notify_alerts(self, alerts: list[dict]) -> None:
        for cb in self._alert_callbacks:
            try:
                cb(alerts)
            except Exception as exc:
                logger.error("Alert callback failed: %s", exc)

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler — begins executing all enabled tasks."""
        with self._lock:
            if self._running:
                return
            self._running = True
            logger.info(
                "Agent scheduler started with %d task(s)",
                len(self._tasks),
            )
            for task in self._tasks.values():
                if task.enabled:
                    self._schedule_next(task)

    def stop(self) -> None:
        """Stop the scheduler — cancels all pending timers."""
        with self._lock:
            self._running = False
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            logger.info("Agent scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Internal scheduling ──────────────────────────────────────────

    def _schedule_next(self, task: ScheduledTask) -> None:
        """Schedule the next execution of a task."""
        if not task.enabled or not self._running:
            return

        if task.schedule_type == ScheduleType.INTERVAL:
            delay = task.interval_seconds
        elif task.schedule_type == ScheduleType.ONE_SHOT:
            delay = task.interval_seconds
        else:
            # cron_like: calculate seconds until next cron_hour:cron_minute
            delay = self._seconds_until_cron(task.cron_hour, task.cron_minute)

        timer = threading.Timer(delay, self._execute_task, args=[task.task_id])
        timer.daemon = True
        timer.start()
        self._timers[task.task_id] = timer

    def _execute_task(self, task_id: str) -> None:
        """Execute a scheduled task and handle results."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.enabled or not self._running:
                return

        handler = self._get_handler(task.handler_name)
        if not handler:
            logger.warning(
                "No handler '%s' for task %s", task.handler_name, task_id,
            )
            return

        task.last_status = TaskStatus.RUNNING
        task.last_run = datetime.now().isoformat()

        try:
            result = handler(**task.handler_args)
            task.run_count += 1
            task.last_status = (
                TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            )
            task.last_result = result.message[:200]

            if not result.success:
                task.failure_count += 1

            # Log execution
            log_entry = {
                "task_id": task_id,
                "task_name": task.name,
                "timestamp": task.last_run,
                "success": result.success,
                "message": result.message[:200],
                "alert_count": len(result.alerts),
            }
            with self._lock:
                self._execution_log.append(log_entry)
                if len(self._execution_log) > self._max_log_size:
                    self._execution_log = self._execution_log[
                        -self._max_log_size:
                    ]

            # Notify alert callbacks
            if result.alerts:
                logger.info(
                    "Task %s generated %d alert(s)",
                    task.name, len(result.alerts),
                )
                self._notify_alerts(result.alerts)

        except Exception as exc:
            task.failure_count += 1
            task.last_status = TaskStatus.FAILED
            task.last_result = str(exc)[:200]
            logger.error(
                "Scheduled task %s failed: %s", task.name, exc, exc_info=True,
            )

        # Reschedule if recurring
        if task.schedule_type != ScheduleType.ONE_SHOT:
            with self._lock:
                self._schedule_next(task)

    @staticmethod
    def _seconds_until_cron(hour: int, minute: int) -> int:
        """Calculate seconds until the next occurrence of hour:minute."""
        now = datetime.now()
        target = now.replace(second=0, microsecond=0)
        if hour >= 0:
            target = target.replace(hour=hour, minute=minute)
        else:
            target = target.replace(minute=minute)
        if target <= now:
            if hour >= 0:
                target += timedelta(days=1)
            else:
                target += timedelta(hours=1)
        return max(1, int((target - now).total_seconds()))

    # ── Execution log ────────────────────────────────────────────────

    def get_execution_log(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(reversed(self._execution_log[-limit:]))

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "running": self._running,
            "task_count": len(self._tasks),
            "tasks": self.list_tasks(),
            "handlers": self.list_handler_catalog(),
        }


# ── Webhook event handler ───────────────────────────────────────────

class WebhookHandler:
    """
    Handles inbound webhook events from AutomationEdge.

    Maps event types to handler functions. Used by the agent server
    to process events like workflow failures, SLA breaches, etc.
    """

    def __init__(self):
        self._handlers: dict[str, Callable[[dict], TaskResult]] = {}
        self._event_log: list[dict] = []
        self._max_log = 200

    def register(
        self, event_type: str, handler: Callable[[dict], TaskResult],
    ) -> None:
        self._handlers[event_type] = handler

    def handle_event(self, event: dict) -> TaskResult:
        """Process an inbound webhook event."""
        event_type = event.get("type", event.get("event_type", "unknown"))
        event_id = event.get("id", str(uuid.uuid4().hex[:8]))

        logger.info("Webhook event: type=%s id=%s", event_type, event_id)

        self._event_log.append({
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "processed": event_type in self._handlers,
        })
        if len(self._event_log) > self._max_log:
            self._event_log = self._event_log[-self._max_log:]

        handler = self._handlers.get(event_type)
        if not handler:
            return TaskResult(
                success=True,
                message=f"No handler for event type '{event_type}' — ignored.",
            )

        try:
            return handler(event)
        except Exception as exc:
            logger.error(
                "Webhook handler for %s failed: %s",
                event_type, exc, exc_info=True,
            )
            return TaskResult(success=False, message=str(exc))

    def get_event_log(self, limit: int = 50) -> list[dict]:
        return list(reversed(self._event_log[-limit:]))


# ── Built-in webhook event handlers ─────────────────────────────────

def _workflow_failure_event(event: dict) -> TaskResult:
    """Handle a workflow failure webhook event."""
    wf_name = event.get("workflow_name", "unknown")
    exec_id = event.get("execution_id", "")
    error = event.get("error", "")

    logger.warning(
        "Workflow failure event: wf=%s exec=%s error=%s",
        wf_name, exec_id, error[:100],
    )

    alerts = [{
        "severity": "error",
        "message": f"Workflow '{wf_name}' failed: {error[:200]}",
        "workflow": wf_name,
        "execution_id": exec_id,
        "source": "webhook",
        "timestamp": datetime.now().isoformat(),
    }]

    return TaskResult(
        success=True,
        message=f"Processed failure event for {wf_name}",
        alerts=alerts,
    )


def _sla_breach_event(event: dict) -> TaskResult:
    """Handle an SLA breach webhook event."""
    wf_name = event.get("workflow_name", "unknown")
    sla_type = event.get("sla_type", "")
    threshold = event.get("threshold", "")

    alerts = [{
        "severity": "warning",
        "message": f"SLA breach on '{wf_name}': {sla_type} exceeded {threshold}",
        "workflow": wf_name,
        "source": "webhook",
        "timestamp": datetime.now().isoformat(),
    }]

    return TaskResult(
        success=True,
        message=f"Processed SLA breach for {wf_name}",
        alerts=alerts,
    )


# ── Module-level singletons ─────────────────────────────────────────

_scheduler: Optional[AgentScheduler] = None
_webhook_handler: Optional[WebhookHandler] = None


def get_scheduler() -> AgentScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
        load_persisted_tasks(_scheduler)
    return _scheduler


def get_webhook_handler() -> WebhookHandler:
    global _webhook_handler
    if _webhook_handler is None:
        _webhook_handler = WebhookHandler()
        # Register built-in event handlers
        _webhook_handler.register("workflow_failure", _workflow_failure_event)
        _webhook_handler.register("sla_breach", _sla_breach_event)
    return _webhook_handler


def setup_default_tasks(scheduler: AgentScheduler | None = None) -> None:
    """
    Register default proactive tasks.
    Called during server startup.
    """
    sched = scheduler or get_scheduler()
    for task_id in (
        "default-health-check",
        "default-wf-monitor",
        "default-daily-summary",
    ):
        sched.remove_task(task_id)

    health_interval = int(get_runtime_value("HEALTH_CHECK_INTERVAL_SECONDS", 300))
    monitored_wfs = get_runtime_value("MONITORED_WORKFLOWS", [])

    # System health check
    sched.add_task(ScheduledTask(
        task_id="default-health-check",
        name="System Health Check",
        description="Periodic system health poll",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=health_interval,
        handler_name="health_check",
        handler_args={"hours": 1},
        enabled=bool(get_runtime_value("ENABLE_PROACTIVE_MONITORING", False)),
        is_system=True,
    ))

    # Workflow monitor (if workflows configured)
    if monitored_wfs:
        sched.add_task(ScheduledTask(
            task_id="default-wf-monitor",
            name="Workflow Monitor",
            description=f"Monitor {len(monitored_wfs)} workflow(s)",
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=health_interval,
            handler_name="workflow_monitor",
            handler_args={"workflows": monitored_wfs},
            enabled=bool(get_runtime_value("ENABLE_PROACTIVE_MONITORING", False)),
            is_system=True,
        ))

    # Daily summary
    sched.add_task(ScheduledTask(
        task_id="default-daily-summary",
        name="Daily Ops Summary",
        description="Generate daily operations summary",
        schedule_type=ScheduleType.CRON_LIKE,
        cron_hour=int(get_runtime_value("DAILY_SUMMARY_HOUR", 8)),
        cron_minute=0,
        handler_name="daily_summary",
        enabled=bool(get_runtime_value("ENABLE_DAILY_SUMMARY", False)),
        is_system=True,
    ))

    logger.info(
        "Default scheduled tasks configured: %d task(s)",
        len(sched.list_tasks()),
    )


def load_persisted_tasks(scheduler: AgentScheduler | None = None) -> None:
    sched = scheduler or get_scheduler()
    for task_payload in get_scheduler_store().list_tasks():
        try:
            task = ScheduledTask.from_dict(task_payload)
            task.is_system = False
            sched.upsert_task(task)
        except Exception as exc:
            logger.warning("Skipping persisted custom task due to load error: %s", exc)
