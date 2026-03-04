"""
Main entry point for AutomationEdge AI Studio.
This script is deployed as a Python project in AI Studio
and exposed via its web chat interface.
"""

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import CONFIG
from config.logging_setup import setup_logging
from gateway.message_gateway import MessageGateway
from rag.engine import get_rag_engine
from state.agent_catalog import get_agent_catalog
from tools.registry import tool_registry

# Import tool modules so they self-register with the registry
import tools.status_tools      # noqa: F401
import tools.log_tools         # noqa: F401
import tools.file_tools        # noqa: F401
import tools.remediation_tools # noqa: F401
import tools.dependency_tools  # noqa: F401
import tools.notification_tools  # noqa: F401
import tools.general_tools  # noqa: F401

app_logger, audit_logger = setup_logging()
gateway = MessageGateway()

# Load dynamic AE tools if enabled.
try:
    reload_summary = tool_registry.reload_automationedge_tools()
    app_logger.info(
        "Dynamic AE tools reload: enabled=%s registered=%s removed=%s skipped=%s collisions=%s",
        reload_summary.get("enabled"),
        reload_summary.get("registered"),
        reload_summary.get("removed"),
        reload_summary.get("skipped"),
        len(reload_summary.get("collisions", [])),
    )
except Exception as e:
    app_logger.warning(f"Dynamic AE tool sync skipped: {e}")

# Keep default agent definition aligned with currently registered tools.
try:
    get_agent_catalog().ensure_default_agent_links(tool_registry.list_tools())
except Exception as e:
    app_logger.warning(f"Agent catalog initialization skipped: {e}")

# Index tools into RAG on startup
try:
    rag = get_rag_engine()
    tool_docs = tool_registry.get_all_rag_documents()
    rag.index_tools(tool_docs)
    app_logger.info("Ops Agent initialized — tools indexed into RAG")
except Exception as e:
    app_logger.warning(f"RAG indexing deferred (DB may not be ready): {e}")


def handle_chat_message(message: str, session_id: str = "default",
                        user_id: str = "",
                        user_role: str = "technical",
                        on_progress=None) -> str:
    """
    Called by AE AI Studio for each incoming chat message.

    Parameters:
        message:    The user's chat message
        session_id: Unique conversation/session identifier
        user_id:    Authenticated user ID from AE
        user_role:  "business" or "technical" (from AE user profile)
        on_progress: optional ``fn(status_text)`` for streaming progress

    Returns:
        Response string to display in chat
    """
    try:
        app_logger.info(
            f"Message from {user_id} [{user_role}]: {message[:100]}..."
        )
        response = gateway.process_message(
            conversation_id=session_id,
            user_message=message,
            user_id=user_id,
            user_role=user_role,
            on_progress=on_progress,
        )
        app_logger.info(f"Response: {response[:100]}...")
        return response

    except Exception as e:
        app_logger.error(f"Unhandled error: {type(e).__name__}: {e}", exc_info=True)
        # In dev, show a short hint so you can fix the root cause
        show_hint = os.environ.get("SHOW_ERROR_HINT", "").lower() in ("1", "true", "yes")
        hint = f" ({type(e).__name__}: {str(e)[:100]})" if show_hint else ""
        return (
            "I encountered an unexpected error. The operations team has "
            "been notified. Please try again or contact support directly."
            + hint
        )


# ── AE AI Studio Integration ──
# Configure AI Studio to call handle_chat_message()

# Pattern B: Flask endpoint (uncomment if AI Studio routes via HTTP)
#
# from flask import Flask, request, jsonify
# app = Flask(__name__)
#
# @app.route("/chat", methods=["POST"])
# def chat_endpoint():
#     data = request.json
#     response = handle_chat_message(
#         message=data.get("message", ""),
#         session_id=data.get("session_id", "default"),
#         user_id=data.get("user_id", ""),
#         user_role=data.get("user_role", "technical"),
#     )
#     return jsonify({"response": response})
#
# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5050)

# Pattern C: Standalone CLI for testing
if __name__ == "__main__":
    print("=" * 60)
    print("  AutomationEdge Ops Agent — Interactive CLI")
    print("=" * 60)
    print("Type 'quit' to exit. Type 'role:business' to switch persona.\n")

    session_id = "cli-test-001"
    user_role = "technical"

    while True:
        try:
            user_input = input(f"[{user_role}] You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.lower() == "quit":
            break
        if user_input.startswith("role:"):
            user_role = user_input.split(":")[1].strip()
            print(f"  Switched to role: {user_role}")
            continue
        if not user_input:
            continue

        response = handle_chat_message(
            message=user_input,
            session_id=session_id,
            user_id="cli_tester",
            user_role=user_role,
        )
        print(f"\n  Agent: {response}\n")
