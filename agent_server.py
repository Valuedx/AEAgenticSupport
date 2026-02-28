"""
Standalone agent HTTP server.
Exposes the ops agent as a REST API so Cognibot custom hooks can
call it via HTTP without needing heavy deps in Python 3.9.

Endpoints:
    POST /chat          JSON request/response (for Cognibot proxy)
    POST /chat/stream   SSE streaming with progress (for webchat)
    GET  /health
    GET  /              Serves webchat.html
"""
from __future__ import annotations

import json
import os
import sys
import logging
import queue
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from main import handle_chat_message

app = Flask(__name__)
CORS(app)
log = logging.getLogger("agent_server")


@app.route("/chat", methods=["POST"])
def chat():
    """Non-streaming endpoint (backwards compat, used by Cognibot proxy)."""
    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "").strip()
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
    """SSE streaming endpoint — sends progress events then the final response."""
    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "").strip()
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
        except Exception as e:
            log.error(f"Agent error: {e}", exc_info=True)
            event_queue.put({
                "event": "done",
                "data": "I encountered an error. Please try again.",
            })

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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: str) -> str:
    escaped = json.dumps(data)
    return f"event: {event}\ndata: {escaped}\n\n"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/webchat", methods=["GET"])
@app.route("/", methods=["GET"])
def webchat_page():
    html_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "webchat.html"
    )
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("AGENT_SERVER_PORT", 5050))
    print(f"Agent server starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
