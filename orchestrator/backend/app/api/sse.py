"""Server-Sent Events endpoint for real-time execution updates.

Clients connect to GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/stream
and receive a stream of JSON events as the workflow executes:

  event: log
  data: {"node_id": "node_2", "status": "running", ...}

  event: status
  data: {"instance_status": "completed"}
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.security.tenant import get_tenant_id
from app.models.workflow import WorkflowInstance, ExecutionLog

router = APIRouter(prefix="/api/v1/workflows", tags=["sse"])
logger = logging.getLogger(__name__)


def _serialize_dt(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


@router.get("/{workflow_id}/instances/{instance_id}/stream")
async def stream_instance(
    workflow_id: str,
    instance_id: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
):
    """Stream execution updates as Server-Sent Events."""

    db = SessionLocal()
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    db.close()

    if not instance:
        raise HTTPException(404, "Instance not found")

    async def event_generator():
        last_log_count = 0
        last_status = None

        while True:
            if await request.is_disconnected():
                break

            db = SessionLocal()
            try:
                inst = db.query(WorkflowInstance).filter_by(id=instance_id).first()
                if not inst:
                    break

                logs = (
                    db.query(ExecutionLog)
                    .filter_by(instance_id=instance_id)
                    .order_by(ExecutionLog.started_at)
                    .all()
                )

                for log in logs[last_log_count:]:
                    log_data = {
                        "id": str(log.id),
                        "node_id": log.node_id,
                        "node_type": log.node_type,
                        "status": log.status,
                        "started_at": log.started_at,
                        "completed_at": log.completed_at,
                        "error": log.error,
                    }
                    yield f"event: log\ndata: {json.dumps(log_data, default=_serialize_dt)}\n\n"

                last_log_count = len(logs)

                if inst.status != last_status:
                    last_status = inst.status
                    status_data = {
                        "instance_status": inst.status,
                        "current_node_id": inst.current_node_id,
                    }
                    yield f"event: status\ndata: {json.dumps(status_data)}\n\n"

                if inst.status in ("completed", "failed", "suspended"):
                    final = {
                        "instance_status": inst.status,
                        "completed_at": inst.completed_at,
                    }
                    yield f"event: done\ndata: {json.dumps(final, default=_serialize_dt)}\n\n"
                    break

            finally:
                db.close()

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
