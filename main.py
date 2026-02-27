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
from tools.registry import tool_registry

# Import tool modules so they self-register with the registry
import tools.status_tools      # noqa: F401
import tools.log_tools         # noqa: F401
import tools.file_tools        # noqa: F401
import tools.remediation_tools # noqa: F401
import tools.dependency_tools  # noqa: F401
import tools.notification_tools  # noqa: F401

app_logger, audit_logger = setup_logging()
gateway = MessageGateway()

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
                        user_role: str = "technical") -> str:
    """
    Called by AE AI Studio for each incoming chat message.

    Parameters:
        message:    The user's chat message
        session_id: Unique conversation/session identifier
        user_id:    Authenticated user ID from AE
        user_role:  "business" or "technical" (from AE user profile)

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
        )
        app_logger.info(f"Response: {response[:100]}...")
        return response

    except Exception as e:
        app_logger.error(f"Unhandled error: {e}", exc_info=True)
        return (
            "I encountered an unexpected error. The operations team has "
            "been notified. Please try again or contact support directly."
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
