"""
Tests for the proactive scheduler and webhook system (Feature 2.2).

Covers:
* ScheduledTask dataclass
* AgentScheduler — add, remove, enable, disable, execution
* WebhookHandler — event processing
"""
from __future__ import annotations

import time
import pytest

from agents.scheduler import (
    AgentScheduler,
    ScheduledTask,
    ScheduleType,
    TaskResult,
    TaskStatus,
    WebhookHandler,
    _workflow_failure_event,
    _sla_breach_event,
)


# ── Tests: ScheduledTask ─────────────────────────────────────────────

class TestScheduledTask:
    def test_defaults(self):
        task = ScheduledTask(name="Test")
        assert task.task_id.startswith("task-")
        assert task.name == "Test"
        assert task.enabled is True
        assert task.schedule_type == ScheduleType.INTERVAL
        assert task.interval_seconds == 300

    def test_to_dict(self):
        task = ScheduledTask(name="Test", handler_name="health_check")
        d = task.to_dict()
        assert d["name"] == "Test"
        assert d["handler_name"] == "health_check"
        assert d["schedule_type"] == "interval"


# ── Tests: AgentScheduler ───────────────────────────────────────────

class TestAgentScheduler:
    def setup_method(self):
        self.scheduler = AgentScheduler()

    def test_add_task(self):
        task = ScheduledTask(name="Test Task", handler_name="test")
        self.scheduler.add_task(task)
        assert len(self.scheduler.list_tasks()) == 1

    def test_remove_task(self):
        task = ScheduledTask(name="Test Task", handler_name="test")
        self.scheduler.add_task(task)
        assert self.scheduler.remove_task(task.task_id) is True
        assert len(self.scheduler.list_tasks()) == 0

    def test_remove_nonexistent(self):
        assert self.scheduler.remove_task("nonexistent") is False

    def test_enable_disable_task(self):
        task = ScheduledTask(name="Test", handler_name="test", enabled=False)
        self.scheduler.add_task(task)
        assert self.scheduler.enable_task(task.task_id) is True
        t = self.scheduler.get_task(task.task_id)
        assert t.enabled is True

        assert self.scheduler.disable_task(task.task_id) is True
        t = self.scheduler.get_task(task.task_id)
        assert t.enabled is False

    def test_enable_nonexistent(self):
        assert self.scheduler.enable_task("nonexistent") is False
        assert self.scheduler.disable_task("nonexistent") is False

    def test_register_custom_handler(self):
        def my_handler(**kwargs):
            return TaskResult(success=True, message="custom handler ran")

        self.scheduler.register_handler("custom", my_handler)
        handler = self.scheduler._get_handler("custom")
        assert handler is not None
        result = handler()
        assert result.success

    def test_builtin_handler_available(self):
        handler = self.scheduler._get_handler("health_check")
        assert handler is not None

    def test_start_stop(self):
        self.scheduler.start()
        assert self.scheduler.is_running
        self.scheduler.stop()
        assert not self.scheduler.is_running

    def test_execution_with_custom_handler(self):
        results = []

        def quick_handler(**kwargs):
            results.append(True)
            return TaskResult(
                success=True,
                message="quick",
                alerts=[{"severity": "info", "message": "test alert"}],
            )

        self.scheduler.register_handler("quick", quick_handler)
        task = ScheduledTask(
            name="Quick Task",
            schedule_type=ScheduleType.ONE_SHOT,
            interval_seconds=1,
            handler_name="quick",
            enabled=True,
        )
        self.scheduler.add_task(task)
        self.scheduler.start()

        # Wait for the task to execute
        time.sleep(2.5)
        self.scheduler.stop()

        assert len(results) >= 1
        t = self.scheduler.get_task(task.task_id)
        assert t.run_count >= 1
        assert t.last_status == TaskStatus.COMPLETED

    def test_alert_callback(self):
        received_alerts = []

        def on_alert(alerts):
            received_alerts.extend(alerts)

        self.scheduler.on_alerts(on_alert)

        def alert_handler(**kwargs):
            return TaskResult(
                success=True,
                message="alert!",
                alerts=[{"severity": "error", "message": "test"}],
            )

        self.scheduler.register_handler("alerter", alert_handler)
        task = ScheduledTask(
            name="Alert Task",
            schedule_type=ScheduleType.ONE_SHOT,
            interval_seconds=1,
            handler_name="alerter",
        )
        self.scheduler.add_task(task)
        self.scheduler.start()

        time.sleep(2.5)
        self.scheduler.stop()

        assert len(received_alerts) >= 1
        assert received_alerts[0]["severity"] == "error"

    def test_to_dict(self):
        self.scheduler.add_task(ScheduledTask(name="T1", handler_name="h1"))
        d = self.scheduler.to_dict()
        assert d["task_count"] == 1
        assert d["running"] is False

    def test_execution_log(self):
        def log_handler(**kwargs):
            return TaskResult(success=True, message="logged")

        self.scheduler.register_handler("log_test", log_handler)
        task = ScheduledTask(
            name="Log Task",
            schedule_type=ScheduleType.ONE_SHOT,
            interval_seconds=1,
            handler_name="log_test",
        )
        self.scheduler.add_task(task)
        self.scheduler.start()
        time.sleep(2.5)
        self.scheduler.stop()

        log = self.scheduler.get_execution_log()
        assert len(log) >= 1
        assert log[0]["task_name"] == "Log Task"

    def test_cron_seconds_calculation(self):
        # Just verify it returns a positive integer
        seconds = AgentScheduler._seconds_until_cron(23, 59)
        assert seconds >= 1


# ── Tests: WebhookHandler ───────────────────────────────────────────

class TestWebhookHandler:
    def setup_method(self):
        self.handler = WebhookHandler()
        self.handler.register("workflow_failure", _workflow_failure_event)
        self.handler.register("sla_breach", _sla_breach_event)

    def test_handle_known_event(self):
        event = {
            "type": "workflow_failure",
            "workflow_name": "InvoiceProcessing",
            "execution_id": "exec-123",
            "error": "File not found",
        }
        result = self.handler.handle_event(event)
        assert result.success
        assert len(result.alerts) == 1
        assert result.alerts[0]["severity"] == "error"

    def test_handle_sla_breach(self):
        event = {
            "type": "sla_breach",
            "workflow_name": "ClaimsProcessing",
            "sla_type": "execution_time",
            "threshold": "30m",
        }
        result = self.handler.handle_event(event)
        assert result.success
        assert len(result.alerts) == 1
        assert result.alerts[0]["severity"] == "warning"

    def test_handle_unknown_event(self):
        event = {"type": "unknown_event"}
        result = self.handler.handle_event(event)
        assert result.success  # Gracefully ignored
        assert "ignored" in result.message.lower()

    def test_event_log(self):
        self.handler.handle_event({"type": "workflow_failure"})
        self.handler.handle_event({"type": "unknown"})
        log = self.handler.get_event_log()
        assert len(log) == 2

    def test_custom_handler(self):
        def custom(event):
            return TaskResult(success=True, message="custom handled")

        self.handler.register("custom_event", custom)
        result = self.handler.handle_event({"type": "custom_event"})
        assert result.success
        assert "custom" in result.message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
