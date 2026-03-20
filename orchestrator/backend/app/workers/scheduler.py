"""Celery Beat schedule trigger service.

Periodically scans workflow definitions for Schedule Trigger nodes and
creates workflow instances for those whose cron expressions are due.

Run alongside the Celery worker:
    celery -A app.workers.celery_app beat --loglevel=info
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from celery.schedules import crontab
from croniter import croniter

from app.workers.celery_app import celery_app
from app.database import SessionLocal
from app.models.workflow import WorkflowDefinition, WorkflowInstance

logger = logging.getLogger(__name__)

celery_app.conf.beat_schedule = {
    "check-scheduled-workflows": {
        "task": "orchestrator.check_scheduled_workflows",
        "schedule": 60.0,
    },
}


@celery_app.task(name="orchestrator.check_scheduled_workflows")
def check_scheduled_workflows():
    """Scan all workflows for schedule trigger nodes that are due."""
    db = SessionLocal()
    try:
        workflows = db.query(WorkflowDefinition).all()
        now = datetime.now(timezone.utc)
        triggered = 0

        for wf in workflows:
            cron_expr = _extract_schedule_cron(wf.graph_json)
            if not cron_expr:
                continue

            if _is_due(cron_expr, now):
                last_run = (
                    db.query(WorkflowInstance)
                    .filter_by(workflow_def_id=wf.id)
                    .order_by(WorkflowInstance.created_at.desc())
                    .first()
                )
                if last_run and (now - last_run.created_at).total_seconds() < 55:
                    continue

                instance = WorkflowInstance(
                    tenant_id=wf.tenant_id,
                    workflow_def_id=wf.id,
                    trigger_payload={"source": "schedule", "cron": cron_expr, "fired_at": now.isoformat()},
                    status="queued",
                )
                db.add(instance)
                db.commit()
                db.refresh(instance)

                from app.workers.tasks import execute_workflow_task
                execute_workflow_task.delay(str(instance.id))
                triggered += 1
                logger.info("Scheduled trigger fired for workflow %s (cron: %s)", wf.id, cron_expr)

        if triggered:
            logger.info("Schedule check complete: %d workflows triggered", triggered)
    except Exception:
        logger.exception("Error in scheduled workflow check")
        db.rollback()
    finally:
        db.close()


def _extract_schedule_cron(graph_json: dict) -> str | None:
    """Find a Schedule Trigger node and return its cron expression."""
    for node in graph_json.get("nodes", []):
        data = node.get("data", {})
        if data.get("label") == "Schedule Trigger" and data.get("nodeCategory") == "trigger":
            return data.get("config", {}).get("cron")
    return None


def _is_due(cron_expr: str, now: datetime) -> bool:
    """Check if a cron expression is due within the last 60 seconds."""
    try:
        cron = croniter(cron_expr, now)
        prev = cron.get_prev(datetime)
        return (now - prev).total_seconds() <= 60
    except (ValueError, KeyError):
        logger.warning("Invalid cron expression: %s", cron_expr)
        return False
