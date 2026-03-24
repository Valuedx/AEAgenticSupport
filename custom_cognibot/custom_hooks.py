"""
Thin proxy hook for AI Studio Cognibot.
Routes incoming webchat/Teams messages to the standalone agent server
via HTTP, keeping the Cognibot Python 3.9 environment dependency-free.
"""
import asyncio
import json
import logging
import os
import threading
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


try:
    from custom.helpers.teams_proactive import (
        save_conversation_ref as _save_conversation_ref,
        send_proactive_sync as _send_proactive_sync,
    )
except ImportError:
    def _save_conversation_ref(activity) -> None:  # noqa: E306
        pass

    def _send_proactive_sync(thread_id, text) -> bool:  # noqa: E306
        return False

try:
    from custom.helpers.activity import extract_user_role as _extract_user_role
except ImportError:
    # Fallback when custom/ is not on the path (standalone cognibot deploy)
    def _extract_user_role(activity) -> str:
        def _s(v) -> str:
            return str(v or "").strip().lower()
        role = _s(getattr(activity, "user_type", None)) or _s(getattr(activity, "user_role", None))
        if not role:
            cd = getattr(activity, "channel_data", None) or getattr(activity, "channelData", None) or {}
            if isinstance(cd, dict):
                role = _s(cd.get("user_role"))
        return "business" if role == "business" else "technical"


def _extract_activity_text(activity) -> str:
    """Return message text, falling back to Adaptive Card Action.Submit value."""
    try:
        text = (getattr(activity, "text", "") or "").strip()
        if text:
            return text
        value = getattr(activity, "value", None)
        if value is None and isinstance(activity, dict):
            value = activity.get("value")
        if isinstance(value, dict):
            action = str(value.get("action") or "").strip().lower()
            if action == "approve":
                return "approve"
            if action in ("reject", "cancel"):
                return action
    except Exception:
        pass
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

        logger.info("AgentProxyDialog: streaming agent for '%s'", text[:80])

        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue = asyncio.Queue()

        def _stream_to_queue():
            evt_name = "message"
            done_emitted = False
            try:
                resp = requests.post(
                    f"{AGENT_SERVER_URL}/chat/stream",
                    json={
                        "message": text,
                        "session_id": conv_id,
                        "user_id": user_id,
                        "user_role": user_role,
                    },
                    stream=True,
                    timeout=AGENT_TIMEOUT,
                )
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if line.startswith("event:"):
                        evt_name = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except Exception:
                            payload = line[5:].strip()
                        loop.call_soon_threadsafe(
                            event_queue.put_nowait, (evt_name, payload)
                        )
                        if evt_name == "done":
                            done_emitted = True
                        evt_name = "message"
            except Exception as e:
                logger.error("AgentProxyDialog stream error: %s", e)
            if not done_emitted:
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    ("done", "Sorry, the agent is temporarily unavailable. Please try again."),
                )

        threading.Thread(target=_stream_to_queue, daemon=True).start()

        reply_text = "Sorry, the agent is temporarily unavailable."
        while True:
            try:
                evt_name, payload = await asyncio.wait_for(
                    event_queue.get(), timeout=float(AGENT_TIMEOUT)
                )
            except asyncio.TimeoutError:
                reply_text = "Sorry, the agent timed out. Please try again."
                break
            if evt_name == "progress":
                await turn_context.send_activity(str(payload))
            elif evt_name == "done":
                reply_text = str(payload)
                break

        logger.info("AgentProxyDialog: sending final reply (%d chars)", len(reply_text))
        await turn_context.send_activity(reply_text)
        return await step_context.cancel_all_dialogs()


class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = [AgentProxyDialog]

    @staticmethod
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

    @staticmethod
    async def storecon_hook(turn_context):
        return None

    @staticmethod
    async def custom_view_hook(request):
        from django.http import HttpResponse
        return HttpResponse(status=400)

    @staticmethod
    async def webchat_join_event_hook(conv_state, user_state, turn_context):
        return None

    @staticmethod
    async def aistudio_dialog_element_hook(conv_state, user_state, turn_context):
        return None

    @staticmethod
    async def api_messages_hook(request, activity):
        _save_conversation_ref(activity)

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
        event_queue: asyncio.Queue = asyncio.Queue()

        def _stream_to_queue():
            evt_name = "message"
            done_emitted = False
            try:
                resp = requests.post(
                    f"{AGENT_SERVER_URL}/chat/stream",
                    json={
                        "message": text,
                        "session_id": conv_id,
                        "user_id": user_id,
                        "user_role": user_role,
                    },
                    stream=True,
                    timeout=AGENT_TIMEOUT,
                )
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if line.startswith("event:"):
                        evt_name = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except Exception:
                            payload = line[5:].strip()
                        loop.call_soon_threadsafe(
                            event_queue.put_nowait, (evt_name, payload)
                        )
                        if evt_name == "done":
                            done_emitted = True
                        evt_name = "message"
            except Exception as e:
                logger.error("api_messages_hook stream error: %s", e)
            if not done_emitted:
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    ("done", "Sorry, the agent is temporarily unavailable. Please try again."),
                )

        threading.Thread(target=_stream_to_queue, daemon=True).start()

        reply_text = "Sorry, the agent is temporarily unavailable."
        while True:
            try:
                evt_name, payload = await asyncio.wait_for(
                    event_queue.get(), timeout=float(AGENT_TIMEOUT)
                )
            except asyncio.TimeoutError:
                reply_text = "Sorry, the agent timed out. Please try again."
                break
            if evt_name == "progress":
                progress_payload = payload if isinstance(payload, (str, dict)) else str(payload)
                _send_proactive_sync(conv_id, progress_payload)
            elif evt_name == "done":
                reply_text = str(payload)
                break
        return {"type": "message", "text": reply_text}

    @staticmethod
    async def api_reply_hook(request, body):
        return body

    @staticmethod
    async def cancel_conv_hook(conv_state, user_state, turn_context):
        return None

    @staticmethod
    async def voice_bot_start_conv_hook(request, file_data):
        return file_data

    @staticmethod
    async def voice_init_conv_hook(conversation_id, body):
        return body or {}

    @staticmethod
    async def voice_end_conv_hook(conversation_id, request=None, activity=None):
        return None

    @staticmethod
    async def sms_bot_start_conv_hook(body):
        return body or {}

    @staticmethod
    async def sms_bot_reply_hook(request, conversation_id, activity_id, end_conversation, response_list):
        return response_list or []

    @staticmethod
    async def whatsapp_data_channel(flow_data):
        return flow_data or {}

    @staticmethod
    async def custom_schedules():
        return None
