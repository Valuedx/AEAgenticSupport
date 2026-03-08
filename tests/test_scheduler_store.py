from __future__ import annotations

from agents.scheduler import AgentScheduler, load_persisted_tasks
from state.scheduler_store import SchedulerStore
import agents.scheduler as scheduler_module


def test_scheduler_store_generates_ids_and_loads_custom_tasks(tmp_path, monkeypatch):
    store = SchedulerStore(path=str(tmp_path / "scheduler_catalog.json"))
    saved = store.upsert_task(
        {
            "name": "Hourly health scan",
            "description": "Checks the platform every hour.",
            "schedule_type": "interval",
            "interval_seconds": 3600,
            "handler_name": "health_check",
            "handler_args": {"hours": 1},
            "enabled": True,
        }
    )

    assert saved["task_id"].startswith("task-")
    assert saved["handler_name"] == "health_check"
    assert saved["handler_args"] == {"hours": 1}

    monkeypatch.setattr(scheduler_module, "get_scheduler_store", lambda: store)

    scheduler = AgentScheduler()
    load_persisted_tasks(scheduler)
    tasks = scheduler.list_tasks()

    assert len(tasks) == 1
    assert tasks[0]["name"] == "Hourly health scan"
    assert tasks[0]["schedule_type"] == "interval"
    assert tasks[0]["interval_seconds"] == 3600
    assert tasks[0]["handler_name"] == "health_check"
    assert tasks[0]["handler_args"] == {"hours": 1}
    assert tasks[0]["managed_by"] == "custom"
