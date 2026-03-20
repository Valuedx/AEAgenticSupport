"""DAG execution engine.

Parses the React Flow JSON graph into an adjacency list, topologically sorts
it, and executes nodes sequentially -- mapping each node's output into the
next node's input variables.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.workflow import WorkflowInstance, ExecutionLog
from app.engine.node_handlers import dispatch_node

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Graph parsing
# ---------------------------------------------------------------------------

def parse_graph(graph_json: dict) -> tuple[dict, dict, dict]:
    """Convert React Flow JSON into execution-friendly structures.

    Returns:
        nodes_map: {node_id: node_dict}
        adj:       {source_id: [target_id, ...]}   (adjacency list)
        in_degree: {node_id: int}
    """
    nodes_list: list[dict] = graph_json.get("nodes", [])
    edges_list: list[dict] = graph_json.get("edges", [])

    nodes_map: dict[str, dict] = {n["id"]: n for n in nodes_list}
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes_list}

    for edge in edges_list:
        src, tgt = edge["source"], edge["target"]
        adj[src].append(tgt)
        in_degree[tgt] = in_degree.get(tgt, 0) + 1

    return nodes_map, dict(adj), in_degree


def topological_sort(nodes_map: dict, adj: dict, in_degree: dict) -> list[str]:
    """Kahn's algorithm -- returns node IDs in a valid execution order."""
    queue: deque[str] = deque()
    for nid, deg in in_degree.items():
        if deg == 0:
            queue.append(nid)

    order: list[str] = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for neighbor in adj.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(nodes_map):
        executed = set(order)
        cycle_nodes = [nid for nid in nodes_map if nid not in executed]
        raise ValueError(f"Graph contains a cycle involving nodes: {cycle_nodes}")

    return order


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_graph(db: Session, instance_id: str) -> None:
    """Run a full workflow instance from start to finish (or until suspension)."""
    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance:
        raise ValueError(f"WorkflowInstance {instance_id} not found")

    instance.status = "running"
    instance.started_at = _utcnow()
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, adj, in_degree = parse_graph(graph)
    order = topological_sort(nodes_map, adj, in_degree)

    context: dict[str, Any] = dict(instance.context_json or {})
    if instance.trigger_payload:
        context["trigger"] = instance.trigger_payload

    _run_from(db, instance, nodes_map, order, context, start_index=0)


def resume_graph(
    db: Session, instance_id: str, approval_payload: dict
) -> None:
    """Resume a suspended workflow from the node that caused suspension."""
    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance or instance.status != "suspended":
        raise ValueError(
            f"WorkflowInstance {instance_id} not found or not suspended"
        )

    instance.status = "running"
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, adj, in_degree = parse_graph(graph)
    order = topological_sort(nodes_map, adj, in_degree)

    context: dict[str, Any] = dict(instance.context_json or {})
    context["approval"] = approval_payload

    current = instance.current_node_id
    start_index = order.index(current) + 1 if current in order else 0

    _run_from(db, instance, nodes_map, order, context, start_index)


def _run_from(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    order: list[str],
    context: dict[str, Any],
    start_index: int,
) -> None:
    """Execute nodes in order starting from start_index."""
    for i in range(start_index, len(order)):
        node_id = order[i]
        node = nodes_map[node_id]
        node_data: dict = node.get("data", {})
        node_category: str = node_data.get("nodeCategory", "action")

        log_entry = ExecutionLog(
            instance_id=instance.id,
            node_id=node_id,
            node_type=f"{node_data.get('nodeCategory', 'unknown')}:{node_data.get('label', '')}",
            status="running",
            input_json=_build_node_input(node_data, context),
            started_at=_utcnow(),
        )
        db.add(log_entry)
        instance.current_node_id = node_id
        db.commit()

        # --- Human-in-the-loop suspension ---
        if node_category == "action" and node_data.get("config", {}).get("approvalMessage") is not None:
            if "approval" not in context:
                instance.status = "suspended"
                instance.context_json = context
                log_entry.status = "suspended"
                db.commit()
                logger.info("Workflow %s suspended at node %s for human approval", instance.id, node_id)
                return

        try:
            output = dispatch_node(node_data, context, instance.tenant_id)
            context[node_id] = output

            log_entry.status = "completed"
            log_entry.output_json = output
            log_entry.completed_at = _utcnow()
            db.commit()

        except Exception as exc:
            log_entry.status = "failed"
            log_entry.error = str(exc)
            log_entry.completed_at = _utcnow()

            instance.status = "failed"
            instance.context_json = context
            instance.completed_at = _utcnow()
            db.commit()
            logger.exception("Node %s failed in workflow %s", node_id, instance.id)
            return

    instance.status = "completed"
    instance.context_json = context
    instance.completed_at = _utcnow()
    db.commit()
    logger.info("Workflow %s completed successfully", instance.id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_node_input(node_data: dict, context: dict[str, Any]) -> dict:
    """Build the input payload for a node from the accumulated context."""
    return {
        "config": node_data.get("config", {}),
        "upstream_outputs": {
            k: v for k, v in context.items() if k.startswith("node_")
        },
        "trigger": context.get("trigger"),
    }
