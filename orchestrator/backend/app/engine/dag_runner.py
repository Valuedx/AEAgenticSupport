"""DAG execution engine — V0.4: branch-aware traversal + parallel execution.

Parses the React Flow JSON graph into a handle-aware adjacency structure,
executes nodes respecting condition branch outcomes (true/false edges),
and runs independent branches in parallel via ThreadPoolExecutor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.workflow import WorkflowInstance, ExecutionLog
from app.engine.node_handlers import dispatch_node

logger = logging.getLogger(__name__)

_MAX_PARALLEL = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Graph parsing (handle-aware)
# ---------------------------------------------------------------------------

class _Edge:
    __slots__ = ("source", "target", "source_handle")

    def __init__(self, source: str, target: str, source_handle: str | None):
        self.source = source
        self.target = target
        self.source_handle = source_handle


def parse_graph(graph_json: dict) -> tuple[dict, list[_Edge]]:
    """Convert React Flow JSON into execution-friendly structures.

    Returns:
        nodes_map: {node_id: node_dict}
        edges:     list of _Edge with sourceHandle info
    """
    nodes_list: list[dict] = graph_json.get("nodes", [])
    edges_list: list[dict] = graph_json.get("edges", [])

    nodes_map: dict[str, dict] = {n["id"]: n for n in nodes_list}
    edges = [
        _Edge(
            source=e["source"],
            target=e["target"],
            source_handle=e.get("sourceHandle"),
        )
        for e in edges_list
    ]
    return nodes_map, edges


def _build_graph_structures(
    nodes_map: dict, edges: list[_Edge]
) -> tuple[dict[str, list[_Edge]], dict[str, list[_Edge]], dict[str, int]]:
    """Build forward adjacency, reverse adjacency, and in-degree maps."""
    forward: dict[str, list[_Edge]] = defaultdict(list)
    reverse: dict[str, list[_Edge]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in nodes_map}

    for edge in edges:
        forward[edge.source].append(edge)
        reverse[edge.target].append(edge)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    return dict(forward), dict(reverse), in_degree


def _detect_cycles(nodes_map: dict, forward: dict, in_degree: dict) -> None:
    """Validate there are no cycles using Kahn's algorithm."""
    from collections import deque

    deg = dict(in_degree)
    queue = deque(nid for nid, d in deg.items() if d == 0)
    visited = 0

    while queue:
        nid = queue.popleft()
        visited += 1
        for edge in forward.get(nid, []):
            deg[edge.target] -= 1
            if deg[edge.target] == 0:
                queue.append(edge.target)

    if visited != len(nodes_map):
        cycle_nodes = [nid for nid in nodes_map if nid not in set()]
        raise ValueError(f"Graph contains a cycle (visited {visited}/{len(nodes_map)} nodes)")


# ---------------------------------------------------------------------------
# Execution — ready-queue model
# ---------------------------------------------------------------------------

def execute_graph(db: Session, instance_id: str) -> None:
    """Run a full workflow instance with branch-aware parallel execution."""
    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance:
        raise ValueError(f"WorkflowInstance {instance_id} not found")

    instance.status = "running"
    instance.started_at = _utcnow()
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)
    _detect_cycles(nodes_map, forward, in_degree)

    context: dict[str, Any] = dict(instance.context_json or {})
    if instance.trigger_payload:
        context["trigger"] = instance.trigger_payload

    _execute_ready_queue(
        db, instance, nodes_map, forward, reverse, in_degree, context,
        skipped=set(),
    )


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
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

    context: dict[str, Any] = dict(instance.context_json or {})
    context["approval"] = approval_payload

    already_executed = set(context.keys()) - {"trigger", "approval"}
    _execute_ready_queue(
        db, instance, nodes_map, forward, reverse, in_degree, context,
        skipped=already_executed,
    )


def _execute_ready_queue(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    forward: dict[str, list[_Edge]],
    reverse: dict[str, list[_Edge]],
    in_degree: dict[str, int],
    context: dict[str, Any],
    skipped: set[str],
) -> None:
    """Process nodes in ready-order, respecting condition branches and
    running independent nodes in parallel."""

    satisfied: dict[str, set[str]] = defaultdict(set)
    pruned: set[str] = set()

    ready: list[str] = []
    for nid, deg in in_degree.items():
        if deg == 0 and nid not in skipped:
            ready.append(nid)
        elif nid in skipped:
            satisfied[nid] = set()
            _propagate_edges(nid, forward, nodes_map, context, satisfied, pruned)

    while ready:
        ready = [nid for nid in ready if nid not in pruned]
        if not ready:
            break

        if len(ready) == 1:
            node_id = ready[0]
            result = _execute_single_node(
                db, instance, nodes_map, node_id, context,
            )
            if result == "suspended":
                return
            if result == "failed":
                return
            _propagate_edges(node_id, forward, nodes_map, context, satisfied, pruned)
        else:
            results = _execute_parallel(
                db, instance, nodes_map, ready, context,
            )
            for node_id, result in results.items():
                if result == "suspended":
                    instance.context_json = context
                    db.commit()
                    return
                if result == "failed":
                    return
            for node_id in ready:
                if results.get(node_id) == "completed":
                    _propagate_edges(node_id, forward, nodes_map, context, satisfied, pruned)

        ready = _find_ready_nodes(
            nodes_map, reverse, satisfied, context, skipped, pruned,
        )

    all_executed = set(k for k in context if k.startswith("node_") or k == "trigger")
    non_pruned = set(nodes_map.keys()) - pruned - skipped
    trigger_nodes = {nid for nid, n in nodes_map.items() if n.get("data", {}).get("nodeCategory") == "trigger"}
    expected = (non_pruned - trigger_nodes) | {nid for nid in trigger_nodes if nid in context}

    if not any(
        db.query(ExecutionLog).filter_by(instance_id=instance.id, status="failed").first()
        for _ in [1]
    ):
        instance.status = "completed"
    instance.context_json = context
    instance.completed_at = _utcnow()
    db.commit()
    logger.info("Workflow %s completed (pruned %d nodes)", instance.id, len(pruned))


def _propagate_edges(
    node_id: str,
    forward: dict[str, list[_Edge]],
    nodes_map: dict,
    context: dict[str, Any],
    satisfied: dict[str, set[str]],
    pruned: set[str],
) -> None:
    """After a node completes, mark downstream edges as satisfied and prune
    edges that don't match a condition branch."""
    node_output = context.get(node_id, {})
    node_data = nodes_map.get(node_id, {}).get("data", {})
    is_condition = (
        node_data.get("nodeCategory") == "logic"
        and node_data.get("label") == "Condition"
    )

    chosen_branch = None
    if is_condition and isinstance(node_output, dict):
        chosen_branch = node_output.get("branch")

    for edge in forward.get(node_id, []):
        if is_condition and chosen_branch is not None:
            if edge.source_handle is not None and edge.source_handle != chosen_branch:
                _prune_subtree(edge.target, forward, pruned)
                continue

        satisfied[edge.target].add(node_id)


def _prune_subtree(
    node_id: str,
    forward: dict[str, list[_Edge]],
    pruned: set[str],
) -> None:
    """Mark a node and all its downstream-only descendants as pruned (skipped)."""
    if node_id in pruned:
        return
    pruned.add(node_id)
    for edge in forward.get(node_id, []):
        _prune_subtree(edge.target, forward, pruned)


def _find_ready_nodes(
    nodes_map: dict,
    reverse: dict[str, list[_Edge]],
    satisfied: dict[str, set[str]],
    context: dict[str, Any],
    skipped: set[str],
    pruned: set[str],
) -> list[str]:
    """Find nodes whose all incoming (non-pruned) edges are satisfied
    and that haven't been executed yet."""
    ready = []
    executed = set(context.keys())

    for nid in nodes_map:
        if nid in executed or nid in skipped or nid in pruned:
            continue

        incoming = reverse.get(nid, [])
        if not incoming:
            continue

        active_sources = {
            e.source for e in incoming if e.source not in pruned
        }
        if not active_sources:
            ready.append(nid)
            continue

        if active_sources <= satisfied.get(nid, set()):
            ready.append(nid)

    return ready


# ---------------------------------------------------------------------------
# Single-node execution
# ---------------------------------------------------------------------------

def _execute_single_node(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    node_id: str,
    context: dict[str, Any],
) -> str:
    """Execute one node. Returns 'completed', 'suspended', or 'failed'."""
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

    if node_category == "action" and node_data.get("config", {}).get("approvalMessage") is not None:
        if "approval" not in context:
            instance.status = "suspended"
            instance.context_json = context
            log_entry.status = "suspended"
            db.commit()
            logger.info("Workflow %s suspended at node %s for human approval", instance.id, node_id)
            return "suspended"

    try:
        output = dispatch_node(node_data, context, instance.tenant_id)
        context[node_id] = output

        log_entry.status = "completed"
        log_entry.output_json = output
        log_entry.completed_at = _utcnow()
        db.commit()
        return "completed"

    except Exception as exc:
        log_entry.status = "failed"
        log_entry.error = str(exc)
        log_entry.completed_at = _utcnow()

        instance.status = "failed"
        instance.context_json = context
        instance.completed_at = _utcnow()
        db.commit()
        logger.exception("Node %s failed in workflow %s", node_id, instance.id)
        return "failed"


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

def _execute_parallel(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    ready_nodes: list[str],
    context: dict[str, Any],
) -> dict[str, str]:
    """Execute multiple independent nodes concurrently.

    Each thread gets its own DB session for writing ExecutionLog entries.
    The shared `context` dict is written to thread-safely since each node
    writes to a unique key (its own node_id).
    """
    results: dict[str, str] = {}

    log_entries: dict[str, ExecutionLog] = {}
    for node_id in ready_nodes:
        node = nodes_map[node_id]
        node_data = node.get("data", {})
        log_entry = ExecutionLog(
            instance_id=instance.id,
            node_id=node_id,
            node_type=f"{node_data.get('nodeCategory', 'unknown')}:{node_data.get('label', '')}",
            status="running",
            input_json=_build_node_input(node_data, context),
            started_at=_utcnow(),
        )
        db.add(log_entry)
        log_entries[node_id] = log_entry
    instance.current_node_id = ready_nodes[0]
    db.commit()

    def _run_node(node_id: str) -> tuple[str, str, dict | None, str | None]:
        node = nodes_map[node_id]
        node_data = node.get("data", {})
        node_category = node_data.get("nodeCategory", "action")

        if node_category == "action" and node_data.get("config", {}).get("approvalMessage") is not None:
            if "approval" not in context:
                return node_id, "suspended", None, None

        try:
            output = dispatch_node(node_data, context, instance.tenant_id)
            return node_id, "completed", output, None
        except Exception as exc:
            logger.exception("Node %s failed in workflow %s", node_id, instance.id)
            return node_id, "failed", None, str(exc)

    with ThreadPoolExecutor(max_workers=min(len(ready_nodes), _MAX_PARALLEL)) as pool:
        futures = {pool.submit(_run_node, nid): nid for nid in ready_nodes}
        for future in as_completed(futures):
            node_id, status, output, error = future.result()
            results[node_id] = status
            log_entry = log_entries[node_id]

            if status == "completed" and output is not None:
                context[node_id] = output
                log_entry.status = "completed"
                log_entry.output_json = output
                log_entry.completed_at = _utcnow()
            elif status == "suspended":
                log_entry.status = "suspended"
                instance.status = "suspended"
                instance.context_json = context
            elif status == "failed":
                log_entry.status = "failed"
                log_entry.error = error
                log_entry.completed_at = _utcnow()
                instance.status = "failed"
                instance.context_json = context
                instance.completed_at = _utcnow()

    db.commit()
    return results


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
