"""
Optional AI Studio Designer-action wrappers.

These functions exist so AI Studio can discover Python Action entrypoints under
``custom/functions/python`` without changing the main production path, which
remains ``custom/custom_hooks.py`` -> ``agent_server /chat/async``.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional

import requests
from asgiref.sync import sync_to_async

try:
    from botbuilder.core import TurnContext
    from aistudiobot.aistudio.dialog.state import (
        AIStudioConvState,
        AIStudioUserState,
    )
except ImportError:
    class TurnContext:
        pass

    class AIStudioConvState:
        pass

    class AIStudioUserState:
        pass

from custom.helpers.activity import extract_user_role
from custom.models import Case

logger = logging.getLogger("support_agent.actions")


def _safe_getattr(obj: Any, name: str, default: Any = "") -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _safe_call(obj: Any, method_name: str, *args, **kwargs) -> Any:
    method = getattr(obj, method_name, None)
    if callable(method):
        try:
            return method(*args, **kwargs)
        except Exception:
            logger.debug("State helper %s failed", method_name, exc_info=True)
    return None


def _conv_get(
    aistudio_conv_state: AIStudioConvState,
    dialog_name: str,
    key: str,
) -> Optional[str]:
    value = _safe_call(
        aistudio_conv_state, "get_dialog_input_as_param", dialog_name, key
    )
    if value not in (None, ""):
        return value
    value = _safe_call(aistudio_conv_state, "get_conv_input_as_param", key)
    if value not in (None, ""):
        return value
    return None


def _conv_set(
    aistudio_conv_state: AIStudioConvState,
    dialog_name: str,
    key: str,
    value: Any,
) -> None:
    _safe_call(
        aistudio_conv_state, "add_dialog_input_as_param", dialog_name, key, value
    )
    _safe_call(aistudio_conv_state, "add_conv_input_as_param", key, value)


def _agent_server_url() -> str:
    return str(os.environ.get("AGENT_SERVER_URL", "http://localhost:5050")).rstrip("/")


def _agent_timeout_seconds() -> int:
    try:
        return int(os.environ.get("AGENT_TIMEOUT", "120"))
    except (TypeError, ValueError):
        return 120


def _extract_text(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
) -> str:
    activity = getattr(context, "activity", None)
    text = str(_safe_getattr(activity, "text", "") or "").strip()
    if text:
        return text
    for key in ("support_text", "user_query", "message", "text", "latest_user_text"):
        value = _conv_get(aistudio_conv_state, dialog_name, key)
        if value:
            return str(value).strip()
    return ""


def _extract_activity_dict(context: TurnContext) -> dict:
    activity = getattr(context, "activity", None)
    frm = _safe_getattr(activity, "from_property", None) or _safe_getattr(
        activity, "from", None
    )
    conv = _safe_getattr(activity, "conversation", None)
    recipient = _safe_getattr(activity, "recipient", None)
    channel_data = (
        _safe_getattr(activity, "channel_data", None)
        or _safe_getattr(activity, "channelData", None)
        or {}
    )
    return {
        "text": str(_safe_getattr(activity, "text", "") or ""),
        "id": str(_safe_getattr(activity, "id", "") or ""),
        "conversation": {"id": str(_safe_getattr(conv, "id", "") or "")},
        "from": {
            "id": str(_safe_getattr(frm, "id", "") or ""),
            "name": str(_safe_getattr(frm, "name", "") or ""),
            "aadObjectId": str(
                _safe_getattr(frm, "aadObjectId", None)
                or _safe_getattr(frm, "aad_object_id", None)
                or ""
            ),
        },
        "recipient": {
            "id": str(_safe_getattr(recipient, "id", "") or ""),
            "name": str(_safe_getattr(recipient, "name", "") or ""),
        },
        "serviceUrl": str(
            _safe_getattr(activity, "service_url", None)
            or _safe_getattr(activity, "serviceUrl", None)
            or ""
        ),
        "channelId": str(
            _safe_getattr(activity, "channel_id", None)
            or _safe_getattr(activity, "channelId", None)
            or ""
        ),
        "channelData": channel_data if isinstance(channel_data, dict) else {},
    }


def _build_proxy_payload(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
) -> dict:
    activity_dict = _extract_activity_dict(context)
    activity = getattr(context, "activity", None)
    text = _extract_text(context, dialog_name, aistudio_conv_state)
    request_id = (
        str(activity_dict.get("id") or "").strip() or str(uuid.uuid4())
    )
    session_id = (
        str(activity_dict.get("conversation", {}).get("id") or "").strip()
        or str(_conv_get(aistudio_conv_state, dialog_name, "conversation_id") or "").strip()
        or f"{dialog_name}-{uuid.uuid4()}"
    )
    user_id = (
        str(activity_dict.get("from", {}).get("id") or "").strip()
        or str(
            _safe_call(aistudio_user_state, "get_user_input_as_param", "user_id") or ""
        ).strip()
        or "unknown-user"
    )
    user_name = (
        str(activity_dict.get("from", {}).get("name") or "").strip()
        or str(
            _safe_call(aistudio_user_state, "get_user_input_as_param", "user_name") or ""
        ).strip()
    )
    channel = str(activity_dict.get("channelId") or "").strip().lower() or "webchat"
    channel_data = activity_dict.get("channelData", {}) or {}
    tenant = channel_data.get("tenant", {}) if isinstance(channel_data.get("tenant"), dict) else {}
    team = channel_data.get("team", {}) if isinstance(channel_data.get("team"), dict) else {}
    teams_channel = channel_data.get("channel", {}) if isinstance(channel_data.get("channel"), dict) else {}

    user_metadata = {
        "channel": channel,
        "service_url": str(activity_dict.get("serviceUrl") or "").strip(),
        "tenant_id": str(tenant.get("id") or "").strip(),
        "team_id": str(team.get("id") or "").strip(),
        "teams_channel_id": str(teams_channel.get("id") or "").strip(),
        "bot_id": str(activity_dict.get("recipient", {}).get("id") or "").strip(),
        "teams_user_id": str(activity_dict.get("from", {}).get("id") or "").strip(),
        "aad_object_id": str(
            activity_dict.get("from", {}).get("aadObjectId") or ""
        ).strip(),
    }
    user_metadata = {key: value for key, value in user_metadata.items() if value}

    reply_channel = {
        "channel": channel,
        "conversation_id": session_id,
        "service_url": str(activity_dict.get("serviceUrl") or "").strip(),
        "tenant_id": str(tenant.get("id") or "").strip(),
        "bot_id": str(activity_dict.get("recipient", {}).get("id") or "").strip(),
        "bot_name": str(activity_dict.get("recipient", {}).get("name") or "").strip(),
        "user_id": user_id,
        "user_name": user_name,
        "auth_header": "",
        "model_conversation_id": session_id,
        "aistudio_chat_channel": "msteams" if channel == "msteams" else "emulator",
        "delivery_mode": "aistudio_api_reply",
    }

    if not reply_channel["bot_name"]:
        reply_channel["bot_name"] = str(
            _conv_get(aistudio_conv_state, dialog_name, "bot_name") or ""
        ).strip()

    payload = {
        "request_id": request_id,
        "message": text,
        "session_id": session_id,
        "user_id": user_id,
        "user_role": extract_user_role(activity or activity_dict),
        "user_name": user_name,
        "user_email": str(
            _safe_call(aistudio_user_state, "get_user_input_as_param", "user_email") or ""
        ).strip(),
        "user_team": str(
            _safe_call(aistudio_user_state, "get_user_input_as_param", "user_team") or ""
        ).strip(),
        "user_metadata": user_metadata,
        "reply_channel": reply_channel,
    }
    return payload


def _dispatch_proxy_turn_sync(payload: dict) -> dict:
    timeout = min(max(_agent_timeout_seconds(), 5), 15)
    resp = requests.post(
        f"{_agent_server_url()}/chat/async",
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    if not isinstance(data, dict) or not data.get("queued"):
        raise RuntimeError("agent_server did not accept the async turn")
    return data


def _load_gateway_state_sync(session_id: str):
    try:
        from state.conversation_state import ConversationState as GatewayConversationState

        return GatewayConversationState.load(session_id)
    except Exception:
        logger.debug("Could not load gateway conversation state", exc_info=True)
        return None


def _build_gateway_status_message_sync(session_id: str) -> Optional[str]:
    state = _load_gateway_state_sync(session_id)
    if state is None or not getattr(state, "exists_in_store", False):
        return None

    lines = []
    phase = getattr(state, "phase", None)
    phase_value = str(getattr(phase, "value", phase) or "").strip()
    if phase_value:
        lines.append(f"- Phase: {phase_value.upper()}")

    summary = str(getattr(state, "summary", "") or "").strip()
    if summary:
        lines.append(f"- Summary: {summary}")

    affected_workflows = list(getattr(state, "affected_workflows", []) or [])
    if affected_workflows:
        lines.append(f"- Workflows: {affected_workflows}")

    pending_action_summary = str(
        getattr(state, "pending_action_summary", "") or ""
    ).strip()
    if pending_action_summary:
        lines.append(f"- Pending action: {pending_action_summary}")

    if getattr(state, "is_human_handoff", False):
        lines.append("- Human handoff: requested")

    if not lines:
        lines.append("No active cases.")

    return f"Current session status:\n{chr(10).join(lines)}"


def _build_status_message_sync(session_id: str) -> str:
    gateway_status = _build_gateway_status_message_sync(session_id)
    if gateway_status:
        return gateway_status

    cases = (
        Case.objects.filter(thread_id=session_id)
        .exclude(state__in=["CLOSED", "CANCELLED"])
        .order_by("-updated_at")[:10]
    )
    summary = "\n".join(
        f"- [{case.case_id}] {case.state} | Workflows: {case.workflows_involved}"
        for case in cases
    ) or "No active cases."
    return f"Current session status:\n{summary}"


async def queue_support_turn(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
) -> Dict[str, Any]:
    """
    Optional Designer action wrapper that queues the current user turn to the
    external ``agent_server`` without changing the main hook-based production path.
    """
    text = _extract_text(context, dialog_name, aistudio_conv_state)
    if not text:
        response = "It looks like your message was empty. How can I help?"
        _conv_set(aistudio_conv_state, dialog_name, "ae_support_queue_status", "empty")
        _conv_set(aistudio_conv_state, dialog_name, "ae_support_response", response)
        _conv_set(aistudio_conv_state, dialog_name, "response", response)
        return {"success": False, "queued": False, "response": response}

    payload = _build_proxy_payload(
        context, dialog_name, aistudio_conv_state, aistudio_user_state
    )
    try:
        await sync_to_async(_dispatch_proxy_turn_sync, thread_sensitive=False)(payload)
    except Exception as exc:
        logger.error("Designer action thin-proxy dispatch failed: %s", exc, exc_info=True)
        response = "I couldn't queue your request right now. Please try again in a moment."
        _conv_set(aistudio_conv_state, dialog_name, "ae_support_queue_status", "error")
        _conv_set(aistudio_conv_state, dialog_name, "ae_support_last_error", str(exc))
        _conv_set(aistudio_conv_state, dialog_name, "ae_support_response", response)
        _conv_set(aistudio_conv_state, dialog_name, "response", response)
        return {
            "success": False,
            "queued": False,
            "response": response,
            "request_id": payload["request_id"],
            "session_id": payload["session_id"],
        }

    response = "I'm working on that now. I'll send the result here shortly."
    _conv_set(aistudio_conv_state, dialog_name, "ae_support_queue_status", "queued")
    _conv_set(aistudio_conv_state, dialog_name, "ae_support_request_id", payload["request_id"])
    _conv_set(aistudio_conv_state, dialog_name, "ae_support_session_id", payload["session_id"])
    _conv_set(aistudio_conv_state, dialog_name, "ae_support_response", response)
    _conv_set(aistudio_conv_state, dialog_name, "response", response)
    return {
        "success": True,
        "queued": True,
        "response": response,
        "request_id": payload["request_id"],
        "session_id": payload["session_id"],
    }


async def get_support_session_status(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
) -> Dict[str, Any]:
    """
    Optional Designer action wrapper that returns the current support-session
    status using limited local helper logic.
    """
    del aistudio_user_state
    activity = getattr(context, "activity", None)
    session_id = (
        str(_safe_getattr(_safe_getattr(activity, "conversation", None), "id", "") or "").strip()
        or str(_conv_get(aistudio_conv_state, dialog_name, "ae_support_session_id") or "").strip()
        or str(_conv_get(aistudio_conv_state, dialog_name, "conversation_id") or "").strip()
    )
    if not session_id:
        response = "Current session status:\nNo active cases."
        _conv_set(aistudio_conv_state, dialog_name, "ae_support_status_response", response)
        _conv_set(aistudio_conv_state, dialog_name, "response", response)
        return {"success": True, "response": response, "session_id": ""}

    response = await sync_to_async(_build_status_message_sync, thread_sensitive=False)(
        session_id
    )
    _conv_set(aistudio_conv_state, dialog_name, "ae_support_status_response", response)
    _conv_set(aistudio_conv_state, dialog_name, "response", response)
    return {"success": True, "response": response, "session_id": session_id}
