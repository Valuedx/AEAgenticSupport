"""
Teams proactive messaging via Bot Framework REST API.

AI Studio manages the Bot Framework adapter, so we cannot call
``BotFrameworkAdapter.continue_conversation()`` directly.  Instead we use the
Bot Framework Channel REST API:

    POST {service_url}/v3/conversations/{conversation_id}/activities

with a bearer token obtained from Microsoft OAuth2.

Usage
-----
On every inbound Teams message call ``save_conversation_ref(activity)`` so
the reference is available later.  To push a message without a user prompt
(progress update, scheduler alert):

    send_proactive_sync(thread_id, "Processing your request…")

or from async code:

    await asyncio.get_event_loop().run_in_executor(
        None, send_proactive_sync, thread_id, "Done."
    )

Environment variables
---------------------
MS_APP_ID       Bot's Microsoft App ID (required for proactive sends)
MS_APP_PASSWORD Bot's Microsoft App Password (required for proactive sends)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger("support_agent.teams_proactive")

_MS_APP_ID = os.environ.get("MS_APP_ID", "")
_MS_APP_PASSWORD = os.environ.get("MS_APP_PASSWORD", "")

# Simple in-process token cache (token, expiry_timestamp)
_token_cache: tuple[str, float] = ("", 0.0)

_TOKEN_URL = (
    "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
)
_TOKEN_SCOPE = "https://api.botframework.com/.default"


# ── OAuth token ──────────────────────────────────────────────────────────────

def _get_bot_token() -> str:
    """Return a valid Bot Framework bearer token, refreshing when expired."""
    global _token_cache
    token, expiry = _token_cache
    if token and time.time() < expiry - 60:
        return token

    if not _MS_APP_ID or not _MS_APP_PASSWORD:
        raise RuntimeError(
            "MS_APP_ID and MS_APP_PASSWORD must be set for proactive Teams messaging"
        )

    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": _MS_APP_ID,
            "client_secret": _MS_APP_PASSWORD,
            "scope": _TOKEN_SCOPE,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    _token_cache = (token, time.time() + expires_in)
    return token


# ── Reference persistence ─────────────────────────────────────────────────────

def extract_conversation_ref(activity) -> Optional[dict]:
    """Pull ConversationReference fields from a Bot Framework Activity.

    Accepts either an object (attribute access) or a plain dict.
    Returns None if the activity lacks the minimum required fields.
    """
    def _g(obj, *keys):
        """Get nested attribute/key, returning '' on miss."""
        for k in keys:
            if obj is None:
                return ""
            if isinstance(obj, dict):
                obj = obj.get(k)
            else:
                obj = getattr(obj, k, None)
        return obj or ""

    service_url = _g(activity, "serviceUrl") or _g(activity, "service_url")
    if isinstance(activity, dict):
        conv_id = _g(activity, "conversation", "id")
        bot_id = _g(activity, "recipient", "id")
        channel_id = _g(activity, "channelId") or _g(activity, "channel_id") or "msteams"
        tenant_id = (
            _g(activity, "channelData", "tenant", "id")
            or _g(activity, "channel_data", "tenant", "id")
        )
        thread_id = conv_id
    else:
        conv_id = _g(activity, "conversation", "id")
        bot_id = _g(activity, "recipient", "id")
        channel_id = getattr(activity, "channel_id", None) or getattr(activity, "channelId", None) or "msteams"
        channel_data = getattr(activity, "channel_data", None) or getattr(activity, "channelData", None) or {}
        tenant_id = (
            (channel_data.get("tenant") or {}).get("id")
            if isinstance(channel_data, dict) else ""
        )
        thread_id = conv_id

    if not service_url or not conv_id:
        return None

    return {
        "thread_id": str(thread_id),
        "service_url": str(service_url).rstrip("/"),
        "conversation_id": str(conv_id),
        "bot_id": str(bot_id),
        "channel_id": str(channel_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
    }


def save_conversation_ref(activity) -> None:
    """Persist (or update) the conversation reference for *activity*'s thread.

    Safe to call on every inbound message — does nothing if the activity
    lacks a service_url (e.g. it came from a non-Teams channel).
    """
    try:
        ref = extract_conversation_ref(activity)
        if not ref:
            return
        from custom.models import TeamsConversationRef
        TeamsConversationRef.objects.update_or_create(
            thread_id=ref["thread_id"],
            defaults={k: v for k, v in ref.items() if k != "thread_id"},
        )
    except Exception as exc:
        logger.warning("save_conversation_ref failed (non-fatal): %s", exc)


def get_conversation_ref(thread_id: str) -> Optional[dict]:
    """Load the stored ConversationReference for *thread_id*, or None."""
    try:
        from custom.models import TeamsConversationRef
        obj = TeamsConversationRef.objects.filter(thread_id=thread_id).first()
        if obj is None:
            return None
        return {
            "thread_id": obj.thread_id,
            "service_url": obj.service_url,
            "conversation_id": obj.conversation_id,
            "bot_id": obj.bot_id,
            "channel_id": obj.channel_id,
            "tenant_id": obj.tenant_id,
        }
    except Exception as exc:
        logger.warning("get_conversation_ref failed: %s", exc)
        return None


# ── Proactive sender ──────────────────────────────────────────────────────────

def send_proactive_sync(thread_id: str, text_or_card) -> bool:
    """Send *text_or_card* to a Teams thread without an incoming user message.

    *text_or_card* may be:
    - a plain string  → sent as ``{"type": "message", "text": "..."}``
    - a dict          → sent as-is (must be a valid Bot Framework Activity)

    Returns True on success, False on any error (non-raising).
    """
    ref = get_conversation_ref(thread_id)
    if not ref:
        logger.warning(
            "send_proactive_sync: no conversation ref for thread %s — "
            "has the user sent at least one message?",
            thread_id,
        )
        return False

    try:
        token = _get_bot_token()
    except Exception as exc:
        logger.warning("send_proactive_sync: cannot obtain bot token: %s", exc)
        return False

    if isinstance(text_or_card, str):
        activity = {"type": "message", "text": text_or_card}
    else:
        activity = dict(text_or_card)

    # Bot Framework requires these fields on the outbound activity
    activity.setdefault("from", {"id": ref["bot_id"]})
    activity.setdefault("conversation", {"id": ref["conversation_id"]})
    activity.setdefault("channelId", ref["channel_id"])
    if ref.get("tenant_id"):
        activity.setdefault(
            "channelData", {"tenant": {"id": ref["tenant_id"]}}
        )

    url = (
        f"{ref['service_url']}/v3/conversations"
        f"/{ref['conversation_id']}/activities"
    )
    try:
        resp = requests.post(
            url,
            json=activity,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug(
            "send_proactive_sync: sent to %s (%d chars)",
            thread_id,
            len(str(text_or_card)),
        )
        return True
    except Exception as exc:
        logger.warning(
            "send_proactive_sync: POST to %s failed: %s", url, exc
        )
        return False
