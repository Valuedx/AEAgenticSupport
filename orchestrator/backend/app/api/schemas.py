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
    deterministic_mode: bool = Field(
        False,
        description=(
            "When True, parallel node batches are submitted and logged in stable "
            "sorted node-ID order. Results are processed in submission order rather "
            "than completion order, giving fully reproducible execution logs. "
            "Slightly reduces throughput for large parallel batches; leave False "
            "for production hot-paths."
        ),
    )


class CallbackRequest(BaseModel):
    approval_payload: dict[str, Any] = Field(default_factory=dict)
    context_patch: dict[str, Any] | None = Field(
        None,
        description=(
            "Optional shallow-merge patch applied to the workflow context before "
            "resuming. Keys in this dict overwrite matching keys in the existing "
            "context_json. Use to inject corrected values (e.g., a fixed node "
            "output) without rerunning the entire workflow from scratch."
        ),
    )


class InstanceContextOut(BaseModel):
    """Current snapshot of a suspended instance exposed for HITL review."""
    instance_id: uuid.UUID
    status: str
    current_node_id: str | None
    approval_message: str | None
    context_json: dict[str, Any]


class RetryRequest(BaseModel):
    """Retry a failed workflow instance from the failed node or a specific node."""
    from_node_id: str | None = Field(
        None, description="Optional node ID to retry from. Defaults to the node that failed."
    )


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
# Instance Checkpoints (per-node context snapshots)
# ---------------------------------------------------------------------------

class CheckpointOut(BaseModel):
    """Summary of a single per-node checkpoint (no context payload)."""
    id: uuid.UUID
    instance_id: uuid.UUID
    node_id: str
    saved_at: datetime | None

    model_config = {"from_attributes": True}


class CheckpointDetailOut(CheckpointOut):
    """Full checkpoint including the context snapshot at that point."""
    context_json: dict[str, Any]


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


# ---------------------------------------------------------------------------
# Conversation Sessions (Stateful Re-Trigger Pattern)
# ---------------------------------------------------------------------------

class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: str | None = None


class ConversationSessionOut(BaseModel):
    session_id: str
    tenant_id: str
    messages: list[ConversationMessage]
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationSessionSummary(BaseModel):
    session_id: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
