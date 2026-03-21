from app.workers.celery_app import celery_app
from app.engine.dag_runner import execute_graph
from app.database import SessionLocal


@celery_app.task(bind=True, name="orchestrator.execute_workflow")
def execute_workflow_task(self, instance_id: str, deterministic_mode: bool = False):
    """Celery task that picks up a queued workflow instance and runs it
    through the DAG execution engine.

    Args:
        deterministic_mode: Forwarded to execute_graph — when True, parallel
            batches are processed in stable sorted node-ID order for
            reproducible execution logs.
    """
    db = SessionLocal()
    try:
        execute_graph(db, instance_id, deterministic_mode=deterministic_mode)
    finally:
        db.close()


@celery_app.task(bind=True, name="orchestrator.resume_workflow")
def resume_workflow_task(
    self,
    instance_id: str,
    approval_payload: dict | None = None,
    context_patch: dict | None = None,
):
    """Resume a suspended workflow (e.g. after human approval).

    Args:
        context_patch: Optional shallow-merge dict applied to context before
            re-entering the ready queue (HITL context edit support).
    """
    db = SessionLocal()
    try:
        from app.engine.dag_runner import resume_graph
        resume_graph(db, instance_id, approval_payload or {}, context_patch=context_patch)
    finally:
        db.close()


@celery_app.task(bind=True, name="orchestrator.retry_workflow")
def retry_workflow_task(self, instance_id: str, from_node_id: str | None = None):
    """Retry a failed workflow instance from the failed node."""
    db = SessionLocal()
    try:
        from app.engine.dag_runner import retry_graph
        retry_graph(db, instance_id, from_node_id)
    finally:
        db.close()


@celery_app.task(bind=True, name="orchestrator.resume_paused_workflow")
def resume_paused_workflow_task(
    self,
    instance_id: str,
    context_patch: dict | None = None,
):
    """Resume a workflow paused between nodes (operator pause, not HITL)."""
    db = SessionLocal()
    try:
        from app.engine.dag_runner import resume_paused_graph

        resume_paused_graph(db, instance_id, context_patch=context_patch)
    finally:
        db.close()

