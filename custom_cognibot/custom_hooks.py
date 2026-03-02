"""
Thin proxy hook for AI Studio Cognibot.
Routes incoming webchat/Teams messages to the standalone agent server
via HTTP, keeping the Cognibot Python 3.9 environment dependency-free.
"""
import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from aistudiobot.hooks import ChatbotHooks
from botbuilder.dialogs import ComponentDialog, WaterfallDialog, WaterfallStepContext

logger = logging.getLogger(__name__)

AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", "http://localhost:5050")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "120"))

_executor = ThreadPoolExecutor(max_workers=4)


def _call_agent_simple(text, conv_id, user_id, user_role="technical"):
    try:
        resp = requests.post(
            f"{AGENT_SERVER_URL}/chat",
            json={
                "message": text,
                "session_id": conv_id,
                "user_id": user_id,
                "user_role": user_role,
            },
            timeout=AGENT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "No response from agent.")
    except requests.RequestException as e:
        logger.error("Agent server call failed: %s", e)
        return "Sorry, the agent is temporarily unavailable. Please try again."


def _extract_user_role(activity) -> str:
    role = ""
    try:
        role = str(getattr(activity, "user_type", "") or "").strip().lower()
    except Exception:
        role = ""

    if not role:
        try:
            role = str(
                getattr(activity, "user_role", "") or ""
            ).strip().lower()
        except Exception:
            role = ""

    if not role:
        try:
            channel_data = (
                getattr(activity, "channel_data", None)
                or getattr(activity, "channelData", None)
                or {}
            )
            if isinstance(channel_data, dict):
                role = str(channel_data.get("user_role", "")).strip().lower()
        except Exception:
            role = ""

    return "business" if role == "business" else "technical"


def _extract_activity_text(activity) -> str:
    try:
        return (getattr(activity, "text", "") or "").strip()
    except Exception:
        return ""


class AgentProxyDialog(ComponentDialog):
    """Dialog that calls the agent server and sends the response.

    root_dialog_hook returns this class so the Cognibot dialog engine
    treats it as a valid dialog (bypassing 'No skill available').
    The actual agent HTTP call and reply happen here, inside the dialog
    pipeline, so the response correctly flows through the DirectLine
    WebSocket channel back to the user.
    """

    def __init__(self, *args, **kwargs):
        super().__init__("AgentProxyDialog")
        self.add_dialog(
            WaterfallDialog("AgentProxyWaterfall", [self._call_agent_step])
        )
        self.initial_dialog_id = "AgentProxyWaterfall"

    @staticmethod
    async def _call_agent_step(step_context: WaterfallStepContext):
        turn_context = step_context.context
        text = _extract_activity_text(turn_context.activity)

        if not text:
            await turn_context.send_activity("I didn't catch that. Could you try again?")
            return await step_context.cancel_all_dialogs()

        conv_id = "webchat-default"
        user_id = "webchat_user"
        user_role = _extract_user_role(turn_context.activity)
        try:
            conv_id = turn_context.activity.conversation.id or conv_id
        except Exception:
            pass
        try:
            frm = getattr(turn_context.activity, "from_property", None)
            if frm is None:
                frm = getattr(turn_context.activity, "from_", None)
            if frm:
                user_id = frm.id or user_id
        except Exception:
            pass

        logger.info("AgentProxyDialog: calling agent for '%s'", text[:80])
        loop = asyncio.get_event_loop()
        try:
            reply_text = await loop.run_in_executor(
                _executor, _call_agent_simple, text, conv_id, user_id, user_role
            )
        except Exception as e:
            logger.error("AgentProxyDialog agent call failed: %s", e)
            reply_text = "Sorry, the agent is temporarily unavailable."

        logger.info("AgentProxyDialog: sending reply (%d chars)", len(reply_text))
        await turn_context.send_activity(reply_text)
        return await step_context.cancel_all_dialogs()


class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = [AgentProxyDialog]

    async def root_dialog_hook(conv_state, user_state, turn_context):
        """Return AgentProxyDialog for all text messages.

        The dialog itself will call the agent server and send the response,
        ensuring it flows through the proper Bot Framework adapter pipeline
        and reaches the client via DirectLine WebSocket.
        """
        text = ""
        try:
            text = (turn_context.activity.text or "").strip()
        except Exception:
            pass

        if not text:
            return None

        logger.info("root_dialog_hook: routing '%s' to AgentProxyDialog", text[:80])
        return AgentProxyDialog

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
        text = _extract_activity_text(activity)
        if not text:
            return None

        conv_id = "webchat-default"
        user_id = "webchat_user"
        user_role = _extract_user_role(activity)

        try:
            conv = getattr(activity, "conversation", None)
            if conv and getattr(conv, "id", None):
                conv_id = conv.id
        except Exception:
            pass

        try:
            frm = getattr(activity, "from_property", None)
            if frm is None:
                frm = getattr(activity, "from_", None)
            if frm and getattr(frm, "id", None):
                user_id = frm.id
        except Exception:
            pass

        loop = asyncio.get_event_loop()
        reply_text = await loop.run_in_executor(
            _executor, _call_agent_simple, text, conv_id, user_id, user_role
        )
        return {"type": "message", "text": reply_text}

    async def api_reply_hook(request, body):
        return body

    async def cancel_conv_hook(conv_state, user_state, turn_context):
        return None

    async def voice_bot_start_conv_hook(request, file_data):
        return file_data

    async def voice_init_conv_hook(conversation_id, body):
        return body or {}

    async def voice_end_conv_hook(conversation_id, request=None, activity=None):
        return None

    async def sms_bot_start_conv_hook(body):
        return body or {}

    async def sms_bot_reply_hook(request, conversation_id, activity_id, end_conversation, response_list):
        return response_list or []

    async def whatsapp_data_channel(flow_data):
        return flow_data or {}

    async def custom_schedules():
        return None
