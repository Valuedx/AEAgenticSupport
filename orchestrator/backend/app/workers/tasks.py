from app.workers.celery_app import celery_app
from app.engine.dag_runner import execute_graph
from app.database import SessionLocal


@celery_app.task(bind=True, name="orchestrator.execute_workflow")
def execute_workflow_task(self, instance_id: str):
    """Celery task that picks up a queued workflow instance and runs it
    through the DAG execution engine."""
    db = SessionLocal()
    try:
        execute_graph(db, instance_id)
    finally:
        db.close()


@celery_app.task(bind=True, name="orchestrator.resume_workflow")
def resume_workflow_task(self, instance_id: str, approval_payload: dict | None = None):
    """Resume a suspended workflow (e.g. after human approval)."""
    db = SessionLocal()
    try:
        from app.engine.dag_runner import resume_graph
        resume_graph(db, instance_id, approval_payload or {})
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

