"""Workflow CRUD and execution endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.security.tenant import get_tenant_id
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowSnapshot, ExecutionLog
from app.api.schemas import (
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowOut,
    ExecuteRequest,
    CallbackRequest,
    RetryRequest,
    InstanceOut,
    InstanceDetailOut,
    InstanceContextOut,
    ExecutionLogOut,
    SnapshotOut,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Workflow Definition CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(
    body: WorkflowCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    from app.engine.config_validator import validate_graph_configs
    warnings = validate_graph_configs(body.graph_json)
    if warnings:
        import logging
        logging.getLogger(__name__).warning("Graph config warnings on create: %s", warnings)

    wf = WorkflowDefinition(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        graph_json=body.graph_json,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    return (
        db.query(WorkflowDefinition)
        .filter_by(tenant_id=tenant_id)
        .order_by(WorkflowDefinition.updated_at.desc())
        .all()
    )


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.patch("/{workflow_id}", response_model=WorkflowOut)
def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    if body.name is not None:
        wf.name = body.name
    if body.description is not None:
        wf.description = body.description
    if body.graph_json is not None:
        from app.engine.config_validator import validate_graph_configs
        warnings = validate_graph_configs(body.graph_json)
        if warnings:
            import logging
            logging.getLogger(__name__).warning("Graph config warnings on update: %s", warnings)
        # Save snapshot of current version before overwriting
        snap = WorkflowSnapshot(
            workflow_def_id=wf.id,
            tenant_id=tenant_id,
            version=wf.version,
            graph_json=wf.graph_json,
        )
        db.add(snap)
        wf.graph_json = body.graph_json
        wf.version += 1

    db.commit()
    db.refresh(wf)
    return wf


@router.delete("/{workflow_id}", status_code=204)
def delete_workflow(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")
    db.delete(wf)
    db.commit()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@router.post("/{workflow_id}/execute", response_model=InstanceOut, status_code=202)
def execute_workflow(
    workflow_id: uuid.UUID,
    body: ExecuteRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    from app.security.rate_limiter import check_execution_quota
    check_execution_quota(db, tenant_id)

    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    instance = WorkflowInstance(
        tenant_id=tenant_id,
        workflow_def_id=wf.id,
        trigger_payload=body.trigger_payload,
        status="queued",
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)

    from app.workers.tasks import execute_workflow_task
    execute_workflow_task.delay(str(instance.id), body.deterministic_mode)

    return instance


@router.post("/{workflow_id}/callback", response_model=InstanceOut)
def callback_workflow(
    workflow_id: uuid.UUID,
    body: CallbackRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    instance = (
        db.query(WorkflowInstance)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id, status="suspended")
        .order_by(WorkflowInstance.created_at.desc())
        .first()
    )
    if not instance:
        raise HTTPException(404, "No suspended instance found for this workflow")

    from app.workers.tasks import resume_workflow_task
    resume_workflow_task.delay(str(instance.id), body.approval_payload, body.context_patch)

    instance.status = "running"
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/{workflow_id}/instances/{instance_id}/retry", response_model=InstanceOut)
def retry_workflow(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    body: RetryRequest = RetryRequest(),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Retry a failed workflow instance from the point of failure.

    Optionally accepts a `from_node_id` to retry from a specific node
    instead of the most recently failed one.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(
            id=instance_id,
            workflow_def_id=workflow_id,
            tenant_id=tenant_id,
            status="failed",
        )
        .first()
    )
    if not instance:
        raise HTTPException(404, "No failed instance found for this workflow")

    from app.workers.tasks import retry_workflow_task
    retry_workflow_task.delay(str(instance.id), body.from_node_id)

    instance.status = "running"
    db.commit()
    db.refresh(instance)
    return instance


# ---------------------------------------------------------------------------
# Status / Logs
# ---------------------------------------------------------------------------

@router.get("/{workflow_id}/status", response_model=list[InstanceOut])
def list_instances(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    return (
        db.query(WorkflowInstance)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id)
        .order_by(WorkflowInstance.created_at.desc())
        .limit(50)
        .all()
    )


@router.get("/{workflow_id}/versions", response_model=list[SnapshotOut])
def list_versions(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """List all saved snapshots for a workflow (excludes graph_json for performance)."""
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    return (
        db.query(WorkflowSnapshot)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id)
        .order_by(WorkflowSnapshot.version.desc())
        .limit(50)
        .all()
    )


@router.post("/{workflow_id}/rollback/{version}", response_model=WorkflowOut)
def rollback_version(
    workflow_id: uuid.UUID,
    version: int,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Restore a workflow to a previously saved snapshot version.

    Creates a new snapshot of the current state before restoring,
    then increments the version number (rollback is a forward operation).
    """
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    snap = (
        db.query(WorkflowSnapshot)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id, version=version)
        .first()
    )
    if not snap:
        raise HTTPException(404, f"Snapshot for version {version} not found")

    # Save current state as a snapshot before restoring
    current_snap = WorkflowSnapshot(
        workflow_def_id=wf.id,
        tenant_id=tenant_id,
        version=wf.version,
        graph_json=wf.graph_json,
    )
    db.add(current_snap)

    wf.graph_json = snap.graph_json
    wf.version += 1

    db.commit()
    db.refresh(wf)
    return wf


@router.get("/{workflow_id}/instances/{instance_id}/context", response_model=InstanceContextOut)
def get_instance_context(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Return the current execution context snapshot for HITL review.

    Strips internal runtime keys (prefixed with '_') before returning.
    When the instance is suspended, also extracts the approvalMessage from
    the suspended node's config so the UI can surface it to the operator.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")

    # Extract approvalMessage from the suspended node's config
    approval_message: str | None = None
    if instance.current_node_id and instance.status == "suspended":
        graph = instance.definition.graph_json
        node = next(
            (n for n in graph.get("nodes", []) if n.get("id") == instance.current_node_id),
            None,
        )
        if node:
            approval_message = node.get("data", {}).get("config", {}).get("approvalMessage")

    # Strip internal runtime keys before exposing to the operator
    context_json = {
        k: v
        for k, v in (instance.context_json or {}).items()
        if not k.startswith("_")
    }

    return InstanceContextOut(
        instance_id=instance.id,
        status=instance.status,
        current_node_id=instance.current_node_id,
        approval_message=approval_message,
        context_json=context_json,
    )


@router.get("/{workflow_id}/instances/{instance_id}", response_model=InstanceDetailOut)
def get_instance_detail(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")

    logs = (
        db.query(ExecutionLog)
        .filter_by(instance_id=instance_id)
        .order_by(ExecutionLog.started_at)
        .all()
    )

    return InstanceDetailOut(
        **{c.name: getattr(instance, c.name) for c in instance.__table__.columns},
        logs=[ExecutionLogOut.model_validate(log) for log in logs],
    )
