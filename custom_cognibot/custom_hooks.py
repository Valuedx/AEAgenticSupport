"""
Thin proxy hook for AI Studio Cognibot.
Routes incoming webchat/Teams messages to the standalone agent server
via HTTP, keeping the Cognibot Python 3.9 environment dependency-free.

Uses the /chat/stream SSE endpoint to receive progress updates and
sends them as intermediate messages to the user (Teams/webchat)
before delivering the final response.
"""
import json
import logging
import os

import requests

from aistudiobot.hooks import ChatbotHooks

logger = logging.getLogger(__name__)

AGENT_SERVER_URL = os.environ.get(
    "AGENT_SERVER_URL", "http://localhost:5050"
)
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "120"))
PROGRESS_ENABLED = os.environ.get("AGENT_PROGRESS_ENABLED", "true").lower() == "true"


def _activity_to_dict(activity):
    if isinstance(activity, dict):
        return activity
    result = {}
    for attr in ("text", "id"):
        result[attr] = getattr(activity, attr, None) or ""
    conv = getattr(activity, "conversation", None)
    result["conversation"] = {"id": getattr(conv, "id", "") or ""} if conv else {}
    frm = getattr(activity, "from_property", None) or getattr(activity, "from", None)
    result["from"] = {"id": getattr(frm, "id", "") or ""} if frm else {}
    return result


def _send_proactive_message(turn_context, text):
    """Send an intermediate message back to the user via Cognibot's turn context.

    This works for both webchat and Teams channels.  The turn_context
    is the Bot Framework TurnContext object passed to the hook.
    """
    try:
        import asyncio
        from botbuilder.core import TurnContext
        from botbuilder.schema import Activity, ActivityTypes

        if not isinstance(turn_context, TurnContext):
            return

        activity = Activity(
            type=ActivityTypes.message,
            text=text,
        )
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(turn_context.send_activity(activity))
        else:
            loop.run_until_complete(turn_context.send_activity(activity))
    except Exception as e:
        logger.debug(f"Proactive message send skipped: {e}")


def _call_agent_streaming(text, conv_id, user_id, turn_context=None):
    """Call the agent server's SSE endpoint and stream progress to the user."""
    try:
        resp = requests.post(
            f"{AGENT_SERVER_URL}/chat/stream",
            json={
                "message": text,
                "session_id": conv_id,
                "user_id": user_id,
                "user_role": "technical",
            },
            timeout=AGENT_TIMEOUT,
            stream=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Agent server call failed: {e}")
        return "Sorry, the agent is temporarily unavailable. Please try again."

    final_response = "No response from agent."
    event_type = ""

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            raw = line[6:]
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = raw

            if event_type == "progress" and PROGRESS_ENABLED and turn_context:
                _send_proactive_message(turn_context, parsed)
            elif event_type == "done":
                final_response = parsed
            event_type = ""

    return final_response


def _call_agent_simple(text, conv_id, user_id):
    """Non-streaming fallback for when SSE is not needed."""
    try:
        resp = requests.post(
            f"{AGENT_SERVER_URL}/chat",
            json={
                "message": text,
                "session_id": conv_id,
                "user_id": user_id,
                "user_role": "technical",
            },
            timeout=AGENT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "No response from agent.")
    except requests.RequestException as e:
        logger.error(f"Agent server call failed: {e}")
        return "Sorry, the agent is temporarily unavailable. Please try again."


class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = []

    async def root_dialog_hook(conv_state, user_state, turn_context):
        return None

    async def storecon_hook(turn_context):
        return None

    async def custom_view_hook(request):
        from django.http import HttpResponse
        return HttpResponse(status=400)

    async def webchat_join_event_hook(conv_state, user_state, turn_context):
        return None

    async def aistudio_dialog_element_hook(conv_state, user_state, turn_context):
        return None

    async def api_messages_hook(request, activity):
        """
        Proxy every incoming message to the standalone agent server.
        Uses SSE streaming when progress is enabled to send intermediate
        status updates to the user while the agent works.
        """
        act = _activity_to_dict(activity)
        text = (act.get("text") or "").strip()
        if not text:
            return None

        conv_id = (act.get("conversation", {}) or {}).get("id", "webchat-default")
        user_id = (act.get("from", {}) or {}).get("id", "webchat_user")

        turn_context = None
        try:
            turn_context = request._turn_context if hasattr(request, '_turn_context') else None
        except Exception:
            pass

        if PROGRESS_ENABLED and turn_context:
            reply_text = _call_agent_streaming(text, conv_id, user_id, turn_context)
        else:
            reply_text = _call_agent_simple(text, conv_id, user_id)

        return {"type": "message", "text": reply_text}

    async def api_reply_hook(request, body):
        return None

    async def cancel_conv_hook(conv_state, user_state, turn_context):
        return None

    async def voice_bot_start_conv_hook(request, file_data):
        return file_data

    async def voice_init_conv_hook(conversation_id, body):
        return None

    async def voice_end_conv_hook(conversation_id, request=None, activity=None):
        return None

    async def sms_bot_start_conv_hook(body):
        return None

    async def sms_bot_reply_hook(request, conversation_id, activity_id, end_conversation, response_list):
        return None

    async def whatsapp_data_channel(flow_data):
        return None

    async def custom_schedules():
        return None
