"""Workflow CRUD and execution endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.security.tenant import get_tenant_id
from app.models.workflow import WorkflowDefinition, WorkflowInstance, ExecutionLog
from app.api.schemas import (
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowOut,
    ExecuteRequest,
    CallbackRequest,
    InstanceOut,
    InstanceDetailOut,
    ExecutionLogOut,
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
    execute_workflow_task.delay(str(instance.id))

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
    resume_workflow_task.delay(str(instance.id), body.approval_payload)

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
