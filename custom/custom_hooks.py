"""
Router hook — api_messages_hook.
The 'brainstem' of the Extension: lock + dedupe + smalltalk gate +
issue classification + routing to support agent.

Implements the AI Studio Cognibot hook contract:
  - Class extends ChatbotHooks
  - All hooks are async static methods
  - api_messages_hook receives (request, activity)
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Optional

import requests
from asgiref.sync import sync_to_async
from django.utils import timezone

try:
    from aistudiobot.hooks import ChatbotHooks
except ImportError:
    class ChatbotHooks:
        """Stub for standalone development outside AI Studio runtime."""
        pass

logger = logging.getLogger("support_agent.hooks")

from custom.helpers.activity import classify_approval_intent, extract_user_role
from custom.helpers.locks import pg_advisory_lock
from custom.helpers.teams_proactive import (
    save_conversation_ref,
    send_proactive_sync,
    send_proactive_via_ref_sync,
)
from custom.helpers.db import is_duplicate_message, mark_message_processed
from custom.helpers.teams import make_text_reply
from custom.models import ConversationState as CogniState, Case, Approval
from custom.functions.python.support_agent import handle_support_turn
from custom.helpers.issue_classifier import (
    classify_message,
    IssueClassification,
    link_cases,
    should_escalate_recurrence,
)


# ── Activity normalisation ──

def _activity_to_dict(activity) -> dict:
    """Normalize a Bot Framework Activity (object or dict) to a plain dict."""
    if isinstance(activity, dict):
        result = dict(activity)
    else:
        result = {}
        for attr in ("text", "id"):
            result[attr] = getattr(activity, attr, None) or ""
        conv = getattr(activity, "conversation", None)
        result["conversation"] = {"id": getattr(conv, "id", "") or ""} if conv else {}
        frm = getattr(activity, "from_property", None) or getattr(activity, "from", None)
        if frm:
            result["from"] = {
                "id": getattr(frm, "id", "") or "",
                "name": getattr(frm, "name", "") or "",
                "aadObjectId": (
                    getattr(frm, "aad_object_id", None)
                    or getattr(frm, "aadObjectId", None)
                    or ""
                ),
            }
        else:
            result["from"] = {}
        value = getattr(activity, "value", None)
        if value is not None:
            result["value"] = value
        result["serviceUrl"] = (
            getattr(activity, "service_url", None)
            or getattr(activity, "serviceUrl", None)
            or ""
        )
        result["channelId"] = (
            getattr(activity, "channel_id", None)
            or getattr(activity, "channelId", None)
            or ""
        )
        channel_data = (
            getattr(activity, "channel_data", None)
            or getattr(activity, "channelData", None)
            or {}
        )
        if channel_data:
            result["channelData"] = channel_data
        rcpt = getattr(activity, "recipient", None)
        if rcpt:
            recipient = {"id": getattr(rcpt, "id", "") or ""}
            recipient_name = getattr(rcpt, "name", "") or ""
            if recipient_name:
                recipient["name"] = recipient_name
            result["recipient"] = recipient
        else:
            result["recipient"] = {}
    # Normalise user_type using the shared helper so all paths agree
    if not result.get("user_type"):
        result["user_type"] = extract_user_role(result)
    return result


def _extract_thread_id(activity: dict) -> str:
    convo = activity.get("conversation", {}) or {}
    return convo.get("id") or "unknown-thread"


def _extract_message_id(activity: dict) -> str:
    return activity.get("id") or str(uuid.uuid4())


def _extract_text(activity: dict) -> str:
    """Return message text, falling back to Adaptive Card Action.Submit value.

    When a user clicks an Adaptive Card button Teams sends an activity where
    ``text`` is empty and the button payload is in ``value``.  Map recognised
    ``value.action`` keys back to the approval words the rest of the pipeline
    expects so that card button clicks are handled identically to typed replies.
    """
    text = (activity.get("text") or "").strip()
    if text:
        return text
    value = activity.get("value")
    if isinstance(value, dict):
        action = str(value.get("action") or "").strip().lower()
        if action == "approve":
            return "approve"
        if action in ("reject", "cancel"):
            return action
    return ""


def _extract_user_id(activity: dict) -> str:
    return (activity.get("from", {}) or {}).get("id", "")


def _extract_user_name(activity: dict) -> str:
    return str((activity.get("from", {}) or {}).get("name", "") or "").strip()


def _extract_channel_id(activity: dict) -> str:
    return str(activity.get("channelId") or "").strip().lower()


def _extract_request_auth_header(request) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        if auth:
            return str(auth).strip()
    meta = getattr(request, "META", None) or {}
    return str(meta.get("HTTP_AUTHORIZATION") or "").strip()


def _extract_request_payload(request) -> dict:
    if request is None:
        return {}

    get_json = getattr(request, "get_json", None)
    if callable(get_json):
        try:
            data = get_json(silent=True)
            if isinstance(data, dict):
                return data
        except TypeError:
            try:
                data = get_json()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        except Exception:
            pass

    for attr in ("json", "data", "_body", "body"):
        raw = getattr(request, attr, None)
        if raw is None:
            continue
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="ignore")
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
    return {}


def _coerce_dict_payload(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_request_additional_info(request) -> dict:
    payload = _extract_request_payload(request)
    return _coerce_dict_payload(payload.get("additionalInfo"))


def _extract_request_chatbot_id(request) -> str:
    payload = _extract_request_payload(request)
    for key in ("chatBotID", "chatbotId", "chat_bot_id"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _is_teams_activity(activity: dict) -> bool:
    return _extract_channel_id(activity) == "msteams"


def _is_smalltalk(text: str) -> bool:
    t = text.lower().strip()
    return (
        t in {"hi", "hello", "hey", "thanks", "thank you", "ok"}
        or t.startswith(("hi ", "hello ", "hey "))
    )


_classify_approval_intent = classify_approval_intent  # shared implementation


def _prepend_result_prefix(result: dict, prefix: str) -> dict:
    if result.get("attachments"):
        card_body = result["attachments"][0].get("content", {}).get("body")
        if isinstance(card_body, list):
            card_body.insert(0, {"type": "TextBlock", "text": prefix.strip(), "wrap": True})
            return result
    result["text"] = prefix + result.get("text", "")
    return result


def _thin_proxy_enabled() -> bool:
    raw = os.environ.get("AI_STUDIO_THIN_PROXY_MODE", "true")
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _agent_server_url() -> str:
    return str(os.environ.get("AGENT_SERVER_URL", "http://localhost:5050")).rstrip("/")


def _agent_timeout_seconds() -> int:
    try:
        return int(os.environ.get("AGENT_TIMEOUT", "120"))
    except (TypeError, ValueError):
        return 120


def _extract_teams_metadata(activity: dict) -> dict:
    frm = activity.get("from", {}) or {}
    channel_data = activity.get("channelData", {}) or {}
    tenant = channel_data.get("tenant", {}) if isinstance(channel_data.get("tenant"), dict) else {}
    team = channel_data.get("team", {}) if isinstance(channel_data.get("team"), dict) else {}
    channel = channel_data.get("channel", {}) if isinstance(channel_data.get("channel"), dict) else {}
    recipient = activity.get("recipient", {}) or {}
    metadata = {
        "channel": _extract_channel_id(activity) or "unknown",
        "service_url": str(activity.get("serviceUrl") or "").strip(),
        "tenant_id": str(tenant.get("id") or "").strip(),
        "team_id": str(team.get("id") or "").strip(),
        "teams_channel_id": str(channel.get("id") or "").strip(),
        "bot_id": str(recipient.get("id") or "").strip(),
        "teams_user_id": str(frm.get("id") or "").strip(),
        "aad_object_id": str(
            frm.get("aadObjectId") or frm.get("aad_object_id") or ""
        ).strip(),
    }
    return {key: value for key, value in metadata.items() if value}


def _build_proxy_payload(activity_dict: dict) -> dict:
    return _build_proxy_payload_for_request(activity_dict, request=None)


def _build_proxy_payload_for_request(activity_dict: dict, request=None, dispatch_state: Optional[dict] = None) -> dict:
    text = (
        str(dispatch_state.get("text") or "").strip()
        if dispatch_state else _extract_text(activity_dict)
    )
    thread_id = (
        str(dispatch_state.get("thread_id") or "").strip()
        if dispatch_state else _extract_thread_id(activity_dict)
    )
    request_id = (
        str(dispatch_state.get("request_id") or "").strip()
        if dispatch_state else _extract_message_id(activity_dict)
    )
    user_id = _extract_user_id(activity_dict) or "unknown-user"
    user_name = _extract_user_name(activity_dict)
    channel = _extract_channel_id(activity_dict) or "webchat"
    teams_meta = _extract_teams_metadata(activity_dict)
    recipient = activity_dict.get("recipient", {}) or {}

    reply_channel = {
        "channel": channel,
        "conversation_id": thread_id,
        "service_url": teams_meta.get("service_url", ""),
        "tenant_id": teams_meta.get("tenant_id", ""),
        "bot_id": teams_meta.get("bot_id", "") or str(recipient.get("id") or "").strip(),
        "bot_name": str(recipient.get("name") or "").strip(),
        "user_id": user_id,
        "user_name": user_name,
        "auth_header": _extract_request_auth_header(request),
        "model_conversation_id": thread_id,
        "aistudio_chat_channel": (
            "msteams" if channel == "msteams" else "emulator"
        ),
        "delivery_mode": "aistudio_api_reply",
    }
    additional_info = _extract_request_additional_info(request)
    if additional_info:
        reply_channel["aistudio_additional_info"] = additional_info
    chatbot_id = _extract_request_chatbot_id(request)
    if chatbot_id:
        reply_channel["aistudio_chatbot_id"] = chatbot_id

    return {
        "request_id": request_id,
        "message": text,
        "session_id": thread_id,
        "user_id": user_id,
        "user_role": extract_user_role(activity_dict),
        "user_name": user_name,
        "user_email": "",
        "user_team": "",
        "user_metadata": teams_meta,
        "reply_channel": reply_channel,
    }


def _dispatch_proxy_turn(payload: dict) -> dict:
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


def _prepare_proxy_dispatch(activity_dict: dict):
    """Persist minimal state and dedupe before handing the turn to agent_server."""
    thread_id = _extract_thread_id(activity_dict)
    request_id = _extract_message_id(activity_dict)
    text = _extract_text(activity_dict)
    if not text:
        return None, make_text_reply(
            "It looks like your message was empty. How can I help?"
        )

    if _is_teams_activity(activity_dict):
        save_conversation_ref(activity_dict)

    with pg_advisory_lock(thread_id):
        if is_duplicate_message(thread_id, request_id):
            return None, make_text_reply(
                "I'm already working on that message. I'll send the result here shortly."
            )

        mark_message_processed(thread_id, request_id)

        conv_state, _ = CogniState.objects.get_or_create(thread_id=thread_id)
        conv_state.last_user_message_id = request_id
        conv_state.updated_at = timezone.now()
        conv_state.save()

    return {
        "thread_id": thread_id,
        "request_id": request_id,
        "text": text,
    }, None


def _normalize_callback_activity(text_or_activity) -> dict:
    if isinstance(text_or_activity, dict):
        activity = dict(text_or_activity)
    else:
        activity = {"type": "message", "text": str(text_or_activity)}
    activity.setdefault("type", "message")
    return activity


def _directline_activity_url(service_url: str, conversation_id: str) -> str:
    base = str(service_url or "").rstrip("/")
    if base.endswith("/v3/directline"):
        return f"{base}/conversations/{conversation_id}/activities"
    return f"{base}/v3/directline/conversations/{conversation_id}/activities"


def _send_directline_reply_sync(reply_channel: dict, text_or_activity) -> bool:
    conversation_id = str(reply_channel.get("conversation_id") or "").strip()
    service_url = str(reply_channel.get("service_url") or "").strip()
    if not conversation_id or not service_url:
        logger.warning(
            "DirectLine callback missing conversation_id/service_url: %s",
            reply_channel,
        )
        return False

    activity = _normalize_callback_activity(text_or_activity)
    activity.setdefault(
        "conversation", {"id": conversation_id}
    )
    activity.setdefault(
        "channelId",
        str(reply_channel.get("channel") or "webchat"),
    )

    bot_id = str(reply_channel.get("bot_id") or "").strip()
    bot_name = str(reply_channel.get("bot_name") or "").strip()
    user_id = str(reply_channel.get("user_id") or "").strip()
    user_name = str(reply_channel.get("user_name") or "").strip()
    if bot_id:
        outbound_from = {"id": bot_id}
        if bot_name:
            outbound_from["name"] = bot_name
        activity.setdefault("from", outbound_from)
    if user_id:
        outbound_recipient = {"id": user_id}
        if user_name:
            outbound_recipient["name"] = user_name
        activity.setdefault("recipient", outbound_recipient)

    headers = {"Content-Type": "application/json"}
    auth_header = str(reply_channel.get("auth_header") or "").strip()
    if auth_header:
        headers["Authorization"] = auth_header

    url = _directline_activity_url(service_url, conversation_id)
    try:
        resp = requests.post(
            url,
            json=activity,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("DirectLine callback POST to %s failed: %s", url, exc)
        return False


def _send_thin_proxy_reply_sync(payload: dict) -> None:
    reply_channel = (
        payload.get("reply_channel")
        if isinstance(payload.get("reply_channel"), dict)
        else {}
    )
    activity = _normalize_callback_activity(payload.get("activity"))
    channel = str(reply_channel.get("channel") or "").strip().lower()

    if channel == "msteams":
        ref = {
            "thread_id": str(reply_channel.get("conversation_id") or "").strip(),
            "service_url": str(reply_channel.get("service_url") or "").strip(),
            "conversation_id": str(reply_channel.get("conversation_id") or "").strip(),
            "bot_id": str(reply_channel.get("bot_id") or "").strip(),
            "channel_id": "msteams",
            "tenant_id": str(reply_channel.get("tenant_id") or "").strip() or None,
        }
        if not send_proactive_via_ref_sync(ref, activity):
            raise RuntimeError("Teams callback delivery failed")
        return

    if not _send_directline_reply_sync(reply_channel, activity):
        raise RuntimeError("DirectLine callback delivery failed")


# ── Synchronous processing core (runs inside sync_to_async) ──

def _process_message_sync(activity_dict: dict, on_progress=None):
    """
    All Django ORM + business logic runs here synchronously.
    Wrapped by sync_to_async in the async hook.

    *on_progress* is an optional ``fn(status_text: str)`` that sends interim
    progress messages back to the Teams channel via proactive send.
    """
    # Persist the conversation reference so proactive sends work later
    save_conversation_ref(activity_dict)

    thread_id = _extract_thread_id(activity_dict)
    msg_id = _extract_message_id(activity_dict)
    text = _extract_text(activity_dict)
    user_id = _extract_user_id(activity_dict)

    if not text:
        return make_text_reply(
            "It looks like your message was empty. How can I help?"
        )

    with pg_advisory_lock(thread_id):
        if is_duplicate_message(thread_id, msg_id):
            return None
        mark_message_processed(thread_id, msg_id)

        if _is_smalltalk(text):
            cs, _ = CogniState.objects.get_or_create(
                thread_id=thread_id
            )
            cs.last_user_message_id = msg_id
            cs.updated_at = timezone.now()
            cs.save()
            return make_text_reply(
                "Hello! How can I help you with support today?"
            )

        # ── Load conversation state and active case ──
        cs, _ = CogniState.objects.get_or_create(thread_id=thread_id)
        active_case = None
        if cs.active_case_id:
            active_case = Case.objects.filter(
                case_id=cs.active_case_id
            ).first()

        # ── Approval with authorization check ──
        if active_case and active_case.state == "WAITING_APPROVAL":
            appr = Approval.objects.filter(
                case_id=active_case.case_id, status="PENDING",
            ).order_by("-created_at").first()

            if appr:
                approval_intent = _classify_approval_intent(text)

                if approval_intent == "approve":
                    if not appr.requested_to:
                        return make_text_reply(
                            "This approval has no authorized reviewers configured. "
                            "Please contact an administrator."
                        )
                    if user_id and user_id not in appr.requested_to:
                        return make_text_reply(
                            "You are not authorized to approve/reject "
                            "this action. Authorized reviewers: "
                            f"{', '.join(appr.requested_to)}"
                        )
                    return handle_support_turn(
                        thread_id=thread_id,
                        teams_message_id=msg_id,
                        on_progress=on_progress,
                        user_text=text,
                        raw_activity=activity_dict,
                    )

                if approval_intent in {"reject", "cancel"}:
                    appr.status = "REJECTED"
                    appr.decided_by = user_id
                    appr.decided_at = timezone.now()
                    appr.save()
                    active_case.state = "PLANNING"
                    active_case.updated_at = timezone.now()
                    active_case.save()
                    return make_text_reply(
                        "Action rejected. How would you like to proceed?"
                    )

        # ── Issue classification ──
        classification, ref_case_id = classify_message(
            thread_id, text, active_case
        )

        if classification == IssueClassification.RECURRENCE:
            old_case = (
                Case.objects.filter(case_id=ref_case_id).first()
                if ref_case_id else None
            )
            if not old_case:
                logger.warning(
                    "RECURRENCE ref_case %s not found — treating as new issue",
                    ref_case_id,
                )
                cs.active_case_id = None
                cs.updated_at = timezone.now()
                cs.save()
                return handle_support_turn(
                    thread_id=thread_id,
                    teams_message_id=msg_id,
                    user_text=text,
                    raw_activity=activity_dict,
                    on_progress=on_progress,
                )

            old_case.recurrence_count += 1
            old_case.updated_at = timezone.now()
            old_case.save()

            if should_escalate_recurrence(old_case):
                old_case.state = "WAITING_ON_TEAM"
                old_case.owner_type = "HUMAN_TEAM"
                old_case.owner_team = "L2_SUPPORT"
                old_case.save()
                return make_text_reply(
                    f"This issue has now recurred "
                    f"{old_case.recurrence_count} times. "
                    f"The previous fix is not holding. "
                    f"Escalating to L2 support for a "
                    f"permanent resolution."
                )

            old_case.state = "PLANNING"
            old_case.resolved_at = None
            old_case.save()
            cs.active_case_id = old_case.case_id
            cs.updated_at = timezone.now()
            cs.save()
            prefix = (
                f"This looks like a recurrence "
                f"(#{old_case.recurrence_count}) of a previous "
                f"issue. "
            )
            if old_case.resolution_summary:
                prefix += (
                    f"Last resolution: "
                    f"{old_case.resolution_summary[:200]}. "
                )
            prefix += (
                "Let me check if the same cause applies.\n\n"
            )
            result = handle_support_turn(
                thread_id=thread_id,
                teams_message_id=msg_id,
                user_text=text,
                raw_activity=activity_dict,
                on_progress=on_progress,
            )
            return _prepend_result_prefix(result, prefix)

        elif classification == IssueClassification.NEW_ISSUE:
            cs.active_case_id = None
            cs.updated_at = timezone.now()
            cs.save()

        elif classification == IssueClassification.RELATED_NEW:
            parent_id = ref_case_id or (
                active_case.case_id if active_case else None
            )
            cs.active_case_id = None
            cs.updated_at = timezone.now()
            cs.save()
            result = handle_support_turn(
                thread_id=thread_id,
                teams_message_id=msg_id,
                user_text=text,
                raw_activity=activity_dict,
                on_progress=on_progress,
            )
            new_cs = CogniState.objects.get(thread_id=thread_id)
            if parent_id and new_cs.active_case_id:
                link_cases(parent_id, new_cs.active_case_id, "CASCADE")
            prefix = (
                "This looks related to a previous issue but "
                "appears to be a separate problem. "
                "Tracking as a linked case.\n\n"
            )
            return _prepend_result_prefix(result, prefix)

        elif classification == IssueClassification.FOLLOWUP:
            target_case = (
                Case.objects.filter(case_id=ref_case_id).first()
                if ref_case_id else active_case
            )
            if target_case and target_case.resolution_summary:
                return make_text_reply(
                    f"Regarding [{target_case.case_id}]: "
                    f"{target_case.resolution_summary}\n\n"
                    f"Would you like me to verify the current status?"
                )
            if target_case:
                return make_text_reply(
                    f"Case [{target_case.case_id}] is currently "
                    f"in state **{target_case.state}**. "
                    f"No resolution recorded yet — would you like "
                    f"me to check the latest status?"
                )

        elif classification == IssueClassification.STATUS_CHECK:
            cases = Case.objects.filter(
                thread_id=thread_id,
            ).exclude(
                state__in=["CLOSED", "CANCELLED"],
            ).order_by("-updated_at")[:10]
            summary = "\n".join(
                f"- [{c.case_id}] {c.state} | "
                f"Workflows: {c.workflows_involved}"
                for c in cases
            ) or "No active cases."
            return make_text_reply(
                f"Current session status:\n{summary}"
            )

        return handle_support_turn(
            thread_id=thread_id,
            teams_message_id=msg_id,
            user_text=text,
            raw_activity=activity_dict,
            on_progress=on_progress,
        )


# ── AI Studio Hook Class ──

class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = []

    @staticmethod
    async def api_messages_hook(request, activity):
        """
        Invoked for every api/messages REST API call.
        Normalises the Activity, delegates to synchronous processing, and
        either dispatches to the thin external proxy or runs inline support
        processing when proxy mode is disabled or unavailable.
        Returns a dict ``{"type": "message", "text": "..."}`` or None.
        """
        activity_dict = _activity_to_dict(activity)
        if _thin_proxy_enabled():
            try:
                dispatch_state, immediate_reply = await sync_to_async(
                    _prepare_proxy_dispatch, thread_sensitive=False
                )(activity_dict)
                if immediate_reply is not None:
                    return immediate_reply

                payload = _build_proxy_payload_for_request(
                    activity_dict,
                    request=request,
                    dispatch_state=dispatch_state,
                )
                await sync_to_async(
                    _dispatch_proxy_turn, thread_sensitive=False
                )(payload)
                return make_text_reply(
                    "I'm working on that now. I'll send the result here shortly."
                )
            except Exception as exc:
                logger.error(
                    "Thin proxy dispatch failed: %s",
                    exc,
                    exc_info=True,
                )
                return make_text_reply(
                    "I couldn't queue your request right now. Please try again in a moment."
                )

        thread_id = _extract_thread_id(activity_dict)

        def _on_progress(status_text: str) -> None:
            send_proactive_sync(thread_id, status_text)

        return await sync_to_async(
            _process_message_sync, thread_sensitive=False
        )(activity_dict, on_progress=_on_progress)

    @staticmethod
    async def api_reply_hook(request, body):
        if not isinstance(body, dict):
            return

        thin_proxy_payload = (
            body.get("thin_proxy_reply")
            if isinstance(body.get("thin_proxy_reply"), dict)
            else None
        )
        if thin_proxy_payload is None:
            return

        await sync_to_async(
            _send_thin_proxy_reply_sync,
            thread_sensitive=False,
        )(thin_proxy_payload)

        additional_info = (
            body.get("additionalInfo")
            if isinstance(body.get("additionalInfo"), dict)
            else {}
        )
        conversation_details = (
            additional_info.get("conversation_details")
            if isinstance(additional_info.get("conversation_details"), dict)
            else {}
        )
        auth_header = str(additional_info.get("auth_header") or "").strip()

        body.clear()
        body["additionalInfo"] = {
            "auth_header": auth_header,
            "conversation_details": conversation_details,
            "uuid": "__thin_proxy_handled__",
        }
