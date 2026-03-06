"""
Standalone agent HTTP server.

Endpoints:
    POST /chat
    POST /chat/stream
    GET  /health
    GET  /, /webchat
    GET  /tools                 (management UI)
    GET  /api/tools
    POST /api/tools/sync
    POST /api/tools/<tool_name>/test
    GET/POST /api/agents
    GET/PUT/DELETE /api/agents/<agent_id>
    GET /api/agents/<agent_id>/interactions
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

from config.settings import CONFIG
from main import handle_chat_message
from state.agent_catalog import get_agent_catalog
from tools.registry import tool_registry

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
CORS(app)
log = logging.getLogger("agent_server")


def _admin_check():
    required = str(CONFIG.get("AGENT_ADMIN_TOKEN", "")).strip()
    if not required:
        return None

    provided = (request.headers.get("X-Admin-Token", "") or "").strip()
    auth = (request.headers.get("Authorization", "") or "").strip()
    if not provided and auth.lower().startswith("bearer "):
        provided = auth.split(" ", 1)[1].strip()

    if provided == required:
        return None
    return jsonify({"error": "unauthorized"}), 401


def _bool_arg(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return text or "agent"


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True, silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"response": "Empty message received."}), 400

    response = handle_chat_message(
        message=message,
        session_id=data.get("session_id", "webchat-default"),
        user_id=data.get("user_id", "webchat_user"),
        user_role=data.get("user_role", "technical"),
    )
    return jsonify({"response": response})


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    data = request.get_json(force=True, silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"response": "Empty message received."}), 400

    session_id = data.get("session_id", "webchat-default")
    user_id = data.get("user_id", "webchat_user")
    user_role = data.get("user_role", "technical")

    event_queue: queue.Queue[dict] = queue.Queue()

    def on_progress(status_text: str):
        event_queue.put({"event": "progress", "data": status_text})

    def run_agent():
        try:
            final = handle_chat_message(
                message=message,
                session_id=session_id,
                user_id=user_id,
                user_role=user_role,
                on_progress=on_progress,
            )
            event_queue.put({"event": "done", "data": final})
        except Exception as exc:
            log.error("Agent error: %s", exc, exc_info=True)
            event_queue.put(
                {
                    "event": "done",
                    "data": "I encountered an error. Please try again.",
                }
            )

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    def generate():
        while True:
            try:
                evt = event_queue.get(timeout=120)
            except queue.Empty:
                yield _sse("progress", "Still working...")
                continue
            yield _sse(evt["event"], evt["data"])
            if evt["event"] == "done":
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/webchat", methods=["GET"])
@app.route("/", methods=["GET"])
def webchat_page():
    html_path = os.path.join(os.path.dirname(__file__), "webchat.html")
    with open(html_path, "r", encoding="utf-8") as handle:
        return handle.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/aistudio-webchat", methods=["GET"])
def aistudio_webchat_page():
    html_path = os.path.join(os.path.dirname(__file__), "aistudio_webchat.html")
    with open(html_path, "r", encoding="utf-8") as handle:
        return handle.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/tools", methods=["GET"])
def tools_page():
    check = _admin_check()
    if check:
        return check
    html_path = os.path.join(os.path.dirname(__file__), "agent_admin.html")
    with open(html_path, "r", encoding="utf-8") as handle:
        return handle.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/tools", methods=["GET"])
def api_tools():
    check = _admin_check()
    if check:
        return check

    do_sync = _bool_arg(request.args.get("sync"), default=False)
    include_inactive = _bool_arg(request.args.get("includeInactive"), default=False)
    sync_summary = None
    if do_sync:
        sync_summary = tool_registry.reload_automationedge_tools(
            include_inactive=include_inactive
        )

    catalog = get_agent_catalog()
    
    # Collect all tool names: from registry (active) + from RAG (searchable)
    all_tool_names = set(tool_registry.list_tools())
    try:
        from rag.engine import get_rag_engine
        rag_hits = get_rag_engine().list_collection("tools")
        for hit in rag_hits:
            meta = hit.get("metadata", {})
            name = meta.get("tool_name")
            if not name:
                raw_id = hit.get("id", "")
                if raw_id.startswith("tool-"):
                    name = raw_id.removeprefix("tool-")
                elif raw_id.startswith("t4-workflow-"):
                    name = raw_id.removeprefix("t4-workflow-")
                else:
                    name = raw_id
            if name:
                all_tool_names.add(name)
    except Exception as exc:
        log.warning("Failed to fetch tool names from RAG for agent linking: %s", exc)

    catalog.ensure_default_agent_links(list(all_tool_names))
    tools_inventory = tool_registry.get_tool_inventory(catalog.get_agent_tool_map())

    # ── Merge tools from RAG database for inventory UI ──
    try:
        # Initialize seen with existing tools from registry
        seen_names = {t["toolName"] for t in tools_inventory}
        
        for hit in rag_hits:
            meta = hit.get("metadata", {})
            name = meta.get("tool_name")
            if not name:
                raw_id = hit.get("id", "")
                if raw_id.startswith("tool-"):
                    name = raw_id.removeprefix("tool-")
                elif raw_id.startswith("t4-workflow-"):
                    name = raw_id.removeprefix("t4-workflow-")
                else:
                    name = raw_id
            
            if name and name not in seen_names:
                tools_inventory.append({
                    "toolName": name,
                    "workflowName": meta.get("workflow_name", ""),
                    "description": meta.get("description", ""),
                    "category": meta.get("category", ""),
                    "tier": meta.get("tier", "medium_risk"),
                    "source": meta.get("source", "automationedge"),
                    "dynamic": True,
                    "active": meta.get("active", True),
                    "tags": meta.get("tags", []),
                    "parameters": meta.get("parameters", []),
                    "linkedAgents": ["ops_orchestrator"], # Since we just linked them all
                })
                seen_names.add(name)
        # Sort again: Source=static first, then Name
        tools_inventory.sort(key=lambda t: (0 if t.get("source") == "static" else 1, t["toolName"]))
    except Exception as exc:
        log.warning("Failed to merge RAG tools into inventory: %s", exc)

    return jsonify(
        {
            "count": len(tools_inventory),
            "tools": tools_inventory,
            "sync": sync_summary,
        }
    )


@app.route("/api/tools/sync", methods=["POST"])
def api_tools_sync():
    check = _admin_check()
    if check:
        return check

    payload = request.get_json(force=True, silent=True) or {}
    include_inactive = _bool_arg(payload.get("includeInactive"), default=False)
    summary = tool_registry.reload_automationedge_tools(
        include_inactive=include_inactive
    )
    get_agent_catalog().ensure_default_agent_links(tool_registry.list_tools())
    return jsonify(summary)


@app.route("/api/tools/<tool_name>/test", methods=["POST"])
def api_tools_test(tool_name: str):
    check = _admin_check()
    if check:
        return check

    payload = request.get_json(force=True, silent=True) or {}
    args = payload.get("args", {})
    if not isinstance(args, dict):
        return jsonify({"error": "'args' must be an object"}), 400

    result = tool_registry.execute(tool_name, **args)
    status = 200 if result.success else 400
    return jsonify(
        {
            "toolName": tool_name,
            "success": result.success,
            "data": result.data,
            "error": result.error,
        }
    ), status


@app.route("/api/agents", methods=["GET"])
def api_agents_list():
    check = _admin_check()
    if check:
        return check

    catalog = get_agent_catalog()
    agents = catalog.list_agents()
    return jsonify({"count": len(agents), "agents": agents})


@app.route("/api/agents", methods=["POST"])
def api_agents_create():
    check = _admin_check()
    if check:
        return check

    payload = request.get_json(force=True, silent=True) or {}
    if not payload.get("agentId"):
        basis = payload.get("usecase") or payload.get("name") or "agent"
        payload["agentId"] = _slugify(str(basis))
    agent = get_agent_catalog().upsert_agent(payload)
    return jsonify(agent), 201


@app.route("/api/agents/<agent_id>", methods=["GET"])
def api_agents_get(agent_id: str):
    check = _admin_check()
    if check:
        return check

    agent = get_agent_catalog().get_agent(agent_id)
    if not agent:
        return jsonify({"error": "agent not found"}), 404
    return jsonify(agent)


@app.route("/api/agents/<agent_id>", methods=["PUT"])
def api_agents_update(agent_id: str):
    check = _admin_check()
    if check:
        return check

    payload = request.get_json(force=True, silent=True) or {}
    payload["agentId"] = agent_id
    agent = get_agent_catalog().upsert_agent(payload)
    return jsonify(agent)


@app.route("/api/agents/<agent_id>", methods=["DELETE"])
def api_agents_delete(agent_id: str):
    check = _admin_check()
    if check:
        return check

    deleted = get_agent_catalog().delete_agent(agent_id)
    if not deleted:
        return jsonify({"error": "agent not found"}), 404
    return jsonify({"deleted": True, "agentId": agent_id})


@app.route("/api/agents/<agent_id>/interactions", methods=["GET"])
def api_agent_interactions(agent_id: str):
    check = _admin_check()
    if check:
        return check

    limit = int(request.args.get("limit", 100))
    rows = get_agent_catalog().list_interactions(agent_id=agent_id, limit=limit)
    return jsonify({"count": len(rows), "interactions": rows})


@app.route("/api/interactions", methods=["GET"])
def api_interactions():
    check = _admin_check()
    if check:
        return check

    agent_id = request.args.get("agentId", "")
    limit = int(request.args.get("limit", 100))
    rows = get_agent_catalog().list_interactions(agent_id=agent_id, limit=limit)
    return jsonify({"count": len(rows), "interactions": rows})


@app.route("/api/sops", methods=["GET"])
def api_sops_list():
    check = _admin_check()
    if check:
        return check

    limit = int(request.args.get("limit", 200))
    try:
        from rag.engine import get_rag_engine
        docs = get_rag_engine().list_collection("sops")
        docs = docs[: max(limit, 1)]
        items = []
        for d in docs:
            md = d.get("metadata") or {}
            items.append(
                {
                    "id": d.get("id", ""),
                    "title": md.get("title") or d.get("id", ""),
                    "tags": md.get("tags", []),
                    "reference_id": md.get("reference_id", ""),
                    "created_at": md.get("created_at", ""),
                    "preview": str(d.get("content", ""))[:220],
                }
            )
        return jsonify({"count": len(items), "sops": items})
    except Exception as exc:
        log.error("Failed to list SOPs: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sops", methods=["POST"])
def api_sops_upsert():
    check = _admin_check()
    if check:
        return check

    payload = request.get_json(force=True, silent=True) or {}
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    sop_id = str(payload.get("id", "")).strip()
    reference_id = str(payload.get("reference_id", "")).strip()
    tags = payload.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []

    if not content:
        return jsonify({"error": "content is required"}), 400

    if not title:
        title = "Untitled SOP"

    if not sop_id:
        sop_id = f"sop-{_slugify(title)}"

    composed_content = f"{title}\n\n{content}"
    doc = {
        "id": sop_id,
        "content": composed_content,
        "metadata": {
            "title": title,
            "reference_id": reference_id,
            "tags": tags,
            "source": "admin_ui",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    try:
        from rag.engine import get_rag_engine
        get_rag_engine().index_documents([doc], collection="sops")
        return jsonify(
            {
                "saved": True,
                "id": sop_id,
                "title": title,
                "reference_id": reference_id,
                "tags": tags,
            }
        )
    except Exception as exc:
        log.error("Failed to save SOP: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ── Multi-agent endpoints ──────────────────────────────────────────────

@app.route("/api/multi-agents", methods=["GET"])
def api_multi_agents():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.agent_registry import get_agent_registry
        registry = get_agent_registry()
        agents = registry.list_agent_info(active_only=False)
        stats = registry.get_invocation_stats()
        return jsonify({
            "count": len(agents),
            "agents": agents,
            "stats": stats,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/multi-agents/<agent_id>/stats", methods=["GET"])
def api_multi_agent_stats(agent_id: str):
    check = _admin_check()
    if check:
        return check
    try:
        from agents.agent_registry import get_agent_registry
        registry = get_agent_registry()
        agent = registry.get(agent_id)
        if not agent:
            return jsonify({"error": "agent not found"}), 404
        stats = registry.get_invocation_stats(agent_id)
        return jsonify({
            "agent": agent.info.to_dict(),
            "stats": stats,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/multi-agents/route", methods=["POST"])
def api_multi_agents_route():
    """Test the multi-agent routing logic without executing."""
    check = _admin_check()
    if check:
        return check
    try:
        from agents.agent_registry import get_agent_registry
        from agents.agent_router import _score_agent

        payload = request.get_json(force=True, silent=True) or {}
        message = str(payload.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400

        registry = get_agent_registry()
        agents = registry.list_agents(active_only=True)
        scored = [
            {
                "agent_id": a.info.agent_id,
                "name": a.info.name,
                "score": round(_score_agent(a, message, None), 3),
                "capabilities": a.info.capabilities,
                "domains": a.info.domains,
            }
            for a in agents
        ]
        scored.sort(key=lambda x: -x["score"])
        return jsonify({
            "message": message[:200],
            "scored_agents": scored,
            "selected": scored[0]["agent_id"] if scored else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Scheduler & Webhook endpoints ──────────────────────────────────────

@app.route("/api/scheduler", methods=["GET"])
def api_scheduler_status():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        sched = get_scheduler()
        return jsonify(sched.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        sched = get_scheduler()
        sched.start()
        return jsonify({"running": True, "tasks": sched.list_tasks()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        sched = get_scheduler()
        sched.stop()
        return jsonify({"running": False})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/tasks", methods=["GET", "POST"])
def api_scheduler_tasks():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler, ScheduledTask, ScheduleType
        sched = get_scheduler()

        if request.method == "GET":
            return jsonify({"tasks": sched.list_tasks()})

        payload = request.get_json(force=True, silent=True) or {}
        task = ScheduledTask(
            name=payload.get("name", "Custom Task"),
            description=payload.get("description", ""),
            schedule_type=ScheduleType(payload.get("schedule_type", "interval")),
            interval_seconds=int(payload.get("interval_seconds", 300)),
            handler_name=payload.get("handler_name", ""),
            handler_args=payload.get("handler_args", {}),
            enabled=payload.get("enabled", True),
        )
        sched.add_task(task)
        return jsonify(task.to_dict()), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/tasks/<task_id>", methods=["DELETE"])
def api_scheduler_task_delete(task_id: str):
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        removed = get_scheduler().remove_task(task_id)
        return jsonify({"removed": removed})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/tasks/<task_id>/enable", methods=["POST"])
def api_scheduler_task_enable(task_id: str):
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        ok = get_scheduler().enable_task(task_id)
        return jsonify({"enabled": ok})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/tasks/<task_id>/disable", methods=["POST"])
def api_scheduler_task_disable(task_id: str):
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        ok = get_scheduler().disable_task(task_id)
        return jsonify({"disabled": ok})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/log", methods=["GET"])
def api_scheduler_log():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_scheduler
        limit = int(request.args.get("limit", 50))
        return jsonify({"log": get_scheduler().get_execution_log(limit)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/webhooks/event", methods=["POST"])
def api_webhook_event():
    """Receive inbound webhook events from AutomationEdge."""
    try:
        from agents.scheduler import get_webhook_handler
        event = request.get_json(force=True, silent=True) or {}
        handler = get_webhook_handler()
        result = handler.handle_event(event)
        return jsonify({
            "success": result.success,
            "message": result.message,
            "alerts": result.alerts,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/webhooks/log", methods=["GET"])
def api_webhook_log():
    check = _admin_check()
    if check:
        return check
    try:
        from agents.scheduler import get_webhook_handler
        limit = int(request.args.get("limit", 50))
        return jsonify({"log": get_webhook_handler().get_event_log(limit)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/webhooks", methods=["POST"])
def api_webhooks():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        from agents.scheduler import get_webhook_handler
        result = get_webhook_handler().handle_event(payload)
        return jsonify({
            "success": result.success,
            "message": result.message,
            "alerts_generated": len(result.alerts)
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/status", methods=["GET"])
def api_scheduler_status():
    try:
        from agents.scheduler import get_scheduler
        return jsonify(get_scheduler().to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scheduler/logs", methods=["GET"])
def api_scheduler_logs():

@app.route("/api/history/search", methods=["GET"])
def api_history_search():
    try:
        query = request.args.get("q", "")
        limit = int(request.args.get("limit", 10))
        from state.conversation_state import ConversationState
        results = ConversationState.search_history(query, limit)
        return jsonify({"results": results})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/export/<conversation_id>", methods=["GET"])
def api_history_export(conversation_id: str):
    check = _admin_check()
    if check: return check
    try:
        fmt = request.args.get("format", "json")
        from state.conversation_state import ConversationState
        state = ConversationState.load(conversation_id)
        if not state:
            return jsonify({"error": "Not found"}), 404
        
        content = state.export_history(fmt)
        return jsonify({
            "conversation_id": conversation_id,
            "format": fmt,
            "content": content
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/summary/<conversation_id>", methods=["POST"])
def api_history_summary(conversation_id: str):
    try:
        from state.conversation_state import ConversationState
        state = ConversationState.load(conversation_id)
        if not state:
            return jsonify({"error": "Not found"}), 404
        
        summary = state.generate_summary(force=True)
        return jsonify({"conversation_id": conversation_id, "summary": summary})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/feedback", methods=["POST"])
def api_history_feedback():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        cid = payload.get("conversation_id")
        rating = payload.get("rating")
        comments = payload.get("comments", "")
        
        if not cid or rating is None:
            return jsonify({"error": "Missing conversation_id or rating"}), 400
            
        from state.conversation_state import ConversationState
        state = ConversationState.load(cid)
        if not state:
            return jsonify({"error": "Not found"}), 404
            
        state.save_feedback(int(rating), comments)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/handoff/<conversation_id>", methods=["POST"])
def api_history_handoff(conversation_id: str):
    try:
        from state.conversation_state import ConversationState
        state = ConversationState.load(conversation_id)
        if not state:
            return jsonify({"error": "Not found"}), 404
        
        state.is_human_handoff = True
        state.save()
        
        # In a real system, this would trigger a notification to a human agent queue
        # For now, we just mark it in the state.
        return jsonify({
            "success": True, 
            "message": "Conversation marked for human handoff."
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Approval & HITL endpoints ──────────────────────────

@app.route("/api/approvals/pending", methods=["GET"])
def api_approvals_pending():
    """List all pending approval requests."""
    try:
        from config.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, conversation_id, request_id, tool_name, tool_params, tier, summary, created_at
                    FROM approval_audit_log
                    WHERE status = 'PENDING'
                    ORDER BY created_at DESC
                """)
                results = []
                for row in cur.fetchall():
                    results.append({
                        "id": row[0],
                        "conversation_id": row[1],
                        "request_id": row[2],
                        "tool_name": row[3],
                        "tool_params": row[4],
                        "tier": row[5],
                        "summary": row[6],
                        "created_at": row[7].isoformat(),
                    })
                return jsonify({"pending": results})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/approvals/decision", methods=["POST"])
def api_approvals_decision():
    """Submit a decision (approve/reject) via API."""
    try:
        payload = request.get_json(force=True, silent=True) or {}
        cid = payload.get("conversation_id")
        decision = payload.get("decision", "").lower() # approve, reject, cancel
        approver_id = payload.get("approver_id", "admin-api")
        
        if not cid or decision not in ("approve", "reject", "cancel"):
            return jsonify({"error": "Missing conversation_id or invalid decision"}), 400
            
        from state.conversation_state import ConversationState
        state = ConversationState.load(cid)
        if not state:
            return jsonify({"error": "Conversation state not found"}), 404
            
        if state.phase != "awaiting_approval":
            return jsonify({"error": f"Conversation is in phase '{state.phase}', not 'awaiting_approval'"}), 400

        # Delegate to the gateway to process the decision as a virtual user message
        from gateway.message_gateway import gateway
        response = gateway.process_message(cid, decision, user_id=approver_id)
        
        return jsonify({
            "success": True,
            "decision": decision,
            "agent_response": response
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/approvals/audit", methods=["GET"])
def api_approvals_audit():
    """Fetch approval audit logs."""
    try:
        limit = int(request.args.get("limit", 50))
        cid = request.args.get("conversation_id")
        
        from config.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT conversation_id, tool_name, status, tier, summary, approver_id, created_at, decided_at
                    FROM approval_audit_log
                """
                params = []
                if cid:
                    query += " WHERE conversation_id = %s"
                    params.append(cid)
                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)
                
                cur.execute(query, tuple(params))
                results = []
                for row in cur.fetchall():
                    results.append({
                        "conversation_id": row[0],
                        "tool_name": row[1],
                        "status": row[2],
                        "tier": row[3],
                        "summary": row[4],
                        "approver_id": row[5],
                        "created_at": row[6].isoformat(),
                        "decided_at": row[7].isoformat() if row[7] else None,
                    })
                return jsonify({"audit": results})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """Get real-time performance metrics."""
    try:
        cid = request.args.get("conversation_id")
        from config.metrics import metrics_collector
        return jsonify(metrics_collector.get_summary(cid))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Initialize scheduler with default tasks
    try:
        from agents.scheduler import get_scheduler, setup_default_tasks
        setup_default_tasks()
        
        # Register session cleanup task
        from state.session_manager import register_cleanup_task
        register_cleanup_task()
        
        if CONFIG.get("ENABLE_PROACTIVE_MONITORING", False):
            get_scheduler().start()
    except Exception as exc:
        log.warning("Scheduler init failed (non-fatal): %s", exc)

    port = int(os.environ.get("AGENT_SERVER_PORT", 5050))
    print(f"Agent server starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


