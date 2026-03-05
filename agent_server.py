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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("AGENT_SERVER_PORT", 5050))
    print(f"Agent server starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

