"""
Persisted custom scheduler task definitions for the admin control center.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any
import uuid

from config.settings import CONFIG


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


_SCHEDULE_TYPES = {"interval", "cron_like", "one_shot"}


class SchedulerStore:
    def __init__(self, path: str | None = None):
        raw_path = path or CONFIG.get(
            "SCHEDULER_CATALOG_PATH", "state/scheduler_catalog.json"
        )
        self.path = Path(raw_path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parent.parent / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        self.path.write_text(
            json.dumps({"version": 1, "updatedAt": _utc_now_iso(), "tasks": []}, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "updatedAt": _utc_now_iso(), "tasks": []}

    def _save(self, payload: dict[str, Any]) -> None:
        payload["updatedAt"] = _utc_now_iso()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_task(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("task payload must be an object")

        task_id = str(payload.get("task_id") or payload.get("taskId") or "").strip()
        if not task_id:
            task_id = f"task-{uuid.uuid4().hex[:8]}"
        schedule_type = str(
            payload.get("schedule_type") or payload.get("scheduleType") or "interval"
        ).strip()
        if schedule_type not in _SCHEDULE_TYPES:
            raise ValueError(
                f"schedule_type must be one of: {', '.join(sorted(_SCHEDULE_TYPES))}"
            )

        try:
            interval_seconds = max(
                1, int(payload.get("interval_seconds") or payload.get("intervalSeconds") or 300)
            )
        except (TypeError, ValueError):
            interval_seconds = 300

        try:
            cron_hour = int(payload.get("cron_hour") or payload.get("cronHour") or -1)
        except (TypeError, ValueError):
            cron_hour = -1

        try:
            cron_minute = int(payload.get("cron_minute") or payload.get("cronMinute") or 0)
        except (TypeError, ValueError):
            cron_minute = 0

        cron_hour = min(23, max(-1, cron_hour))
        cron_minute = min(59, max(0, cron_minute))
        handler_name = str(
            payload.get("handler_name") or payload.get("handlerName") or ""
        ).strip()
        if not handler_name:
            raise ValueError("handler_name is required")

        return {
            "task_id": task_id,
            "name": str(payload.get("name", "Custom Task")).strip() or "Custom Task",
            "description": str(payload.get("description", "")).strip(),
            "schedule_type": schedule_type,
            "interval_seconds": interval_seconds,
            "cron_hour": cron_hour,
            "cron_minute": cron_minute,
            "enabled": _normalize_bool(payload.get("enabled", True)),
            "handler_name": handler_name,
            "handler_args": _normalize_dict(payload.get("handler_args") or payload.get("handlerArgs")),
            "created_at": str(payload.get("created_at") or payload.get("createdAt") or _utc_now_iso()).strip(),
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._load()
        tasks = payload.get("tasks", []) or []
        return [deepcopy(task) for task in tasks if isinstance(task, dict)]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        clean_id = str(task_id or "").strip()
        return next((task for task in self.list_tasks() if task.get("task_id") == clean_id), None)

    def upsert_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_task(payload)
        with self._lock:
            current = self._load()
            tasks = [task for task in (current.get("tasks", []) or []) if isinstance(task, dict)]
            task_id = normalized.get("task_id", "")
            if task_id:
                replaced = False
                for index, task in enumerate(tasks):
                    if task.get("task_id") == task_id:
                        tasks[index] = normalized
                        replaced = True
                        break
                if not replaced:
                    tasks.append(normalized)
            else:
                tasks.append(normalized)
            current["tasks"] = tasks
            self._save(current)
        return deepcopy(normalized)

    def delete_task(self, task_id: str) -> bool:
        clean_id = str(task_id or "").strip()
        with self._lock:
            current = self._load()
            tasks = [task for task in (current.get("tasks", []) or []) if isinstance(task, dict)]
            kept = [task for task in tasks if task.get("task_id") != clean_id]
            removed = len(kept) != len(tasks)
            current["tasks"] = kept
            self._save(current)
        return removed


_scheduler_store: SchedulerStore | None = None


def get_scheduler_store() -> SchedulerStore:
    global _scheduler_store
    if _scheduler_store is None:
        _scheduler_store = SchedulerStore()
    return _scheduler_store
