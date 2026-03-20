"""Pydantic request/response schemas for the orchestrator API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Workflow Definitions
# ---------------------------------------------------------------------------

class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    graph_json: dict[str, Any]


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    graph_json: dict[str, Any] | None = None


class WorkflowOut(BaseModel):
    id: uuid.UUID
    tenant_id: str
    name: str
    description: str | None
    graph_json: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Workflow Instances (Executions)
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    trigger_payload: dict[str, Any] | None = None


class CallbackRequest(BaseModel):
    approval_payload: dict[str, Any] = Field(default_factory=dict)


class InstanceOut(BaseModel):
    id: uuid.UUID
    tenant_id: str
    workflow_def_id: uuid.UUID
    status: str
    current_node_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionLogOut(BaseModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    node_id: str
    node_type: str
    status: str
    input_json: dict[str, Any] | None
    output_json: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class InstanceDetailOut(InstanceOut):
    logs: list[ExecutionLogOut] = []


# ---------------------------------------------------------------------------
# Workflow Snapshots (version history)
# ---------------------------------------------------------------------------

class SnapshotOut(BaseModel):
    id: uuid.UUID
    workflow_def_id: uuid.UUID
    version: int
    saved_at: datetime | None

    model_config = {"from_attributes": True}


class SnapshotDetailOut(SnapshotOut):
    graph_json: dict[str, Any]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class ToolOut(BaseModel):
    name: str
    title: str
    description: str
    category: str
    safety_tier: str
    tags: list[str] = []
