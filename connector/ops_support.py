from __future__ import annotations
import logging
import os
import sys

from botbuilder.core import TurnContext  # type: ignore[import]
from aistudiobot.aistudio.dialog.state import (  # type: ignore[import]
    AIStudioConvState,
    AIStudioUserState,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Direct import from agent_server.py (D:\AEAgenticSupport\agent_server.py)
#
# This replaces the old HTTP-proxy approach so the AI Studio MCP process
# uses the updated agent code in-process, without going through the Flask
# server running on port 5050.
#
# Path resolution:  ops_support.py lives at:
#   .../AI_Studio_Local/Chatbot-Webservice/cognibot/aistudiobot/aistudio/
#          functions/python/ops_support.py
# AEAgenticSupport is 7 levels above the python/ folder.
#
# You can also override by setting:
#   AGENT_ROOT=D:\AEAgenticSupport   (in your .env or launch config)
# ---------------------------------------------------------------------------

_AGENT_ROOT = os.environ.get(
    "AGENT_ROOT",
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            os.pardir,   # functions/
            os.pardir,   # aistudio/
            os.pardir,   # aistudiobot/
            os.pardir,   # cognibot/
            os.pardir,   # Chatbot-Webservice/
            os.pardir,   # AI_Studio_Local/
            os.pardir,   # AEAgenticSupport/
        )
    ),
)

if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

# Primary import: handle_chat_message from agent_server.py
# (agent_server.py itself imports it from main.py — same function, one hop)
_DIRECT_MODE = False
_backend_initialized = False
handle_chat_message = None

try:
    # Import handle_chat_message as re-exported by agent_server.py
    from agent_server import handle_chat_message as _hcm, init_backend  # type: ignore[import]  # noqa: E402

    handle_chat_message = _hcm
    _DIRECT_MODE = True
    logger.info(
        "ops_support: direct mode active — using handle_chat_message "
        "from agent_server.py at %s",
        _AGENT_ROOT,
    )

    # Initialise backend services (scheduler, cleanup) once, in-process.
    # This is safe to call multiple times; we guard with a flag.
    if not _backend_initialized:
        try:
            init_backend()
            _backend_initialized = True
            logger.info("ops_support: init_backend() completed successfully.")
        except Exception as _init_err:
            logger.warning(
                "ops_support: init_backend() failed (non-fatal): %s", _init_err
            )

except Exception as _import_err:
    logger.warning(
        "ops_support: could not import from agent_server.py at %s (%s). "
        "Falling back to HTTP proxy.",
        _AGENT_ROOT,
        _import_err,
    )

# ---------------------------------------------------------------------------
# HTTP-proxy fallback — used only when direct import is unavailable
# ---------------------------------------------------------------------------
import json
import requests  # type: ignore[import]

AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", "http://localhost:5050")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "120"))


async def _call_agent(
    session_id: str,
    user_text: str,
    user_id: str = "webchat_user",
    user_role: str = "technical",
    user_name: str = "",
    user_email: str = "",
    user_team: str = "",
    user_metadata: dict | None = None,
) -> str:
    """
    Primary path : call handle_chat_message() directly from agent_server.py.
    Fallback path: forward via HTTP POST to the agent_server /chat endpoint.
    """
    if _DIRECT_MODE and handle_chat_message is not None:
        try:
            print(
                f"\n[DEBUG-OPS] Direct call → agent_server.handle_chat_message"
                f"(session={session_id!r}, user={user_id!r})\n"
            )
            response = handle_chat_message(
                message=user_text,
                session_id=session_id,
                user_id=user_id,
                user_role=user_role,
                user_name=user_name,
                user_email=user_email,
                user_team=user_team,
                user_metadata=user_metadata or {},
            )
            resp_str: str = str(response)
            print(f"[DEBUG-OPS] Direct call reply: {resp_str[:200]}\n")
            return response
        except Exception as exc:
            logger.error("ops_support: direct call failed: %s", exc, exc_info=True)
            print(f"[DEBUG-OPS] Direct call error: {exc}\n")
            return "Sorry, I encountered an error processing your request. Please try again."

    # --- HTTP fallback ---
    try:
        data = {
            "message": user_text,
            "session_id": session_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_name": user_name,
            "user_email": user_email,
            "user_team": user_team,
            "user_metadata": user_metadata or {},
        }
        payload = json.dumps(data, default=str)
        print(f"\n[DEBUG-OPS] HTTP fallback → Agent Server {AGENT_SERVER_URL}: {payload}\n")

        resp = requests.post(
            f"{AGENT_SERVER_URL}/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=AGENT_TIMEOUT,
        )
        resp.raise_for_status()
        resp_json = resp.json()
        print(f"[DEBUG-OPS] Agent Server reply: {resp_json}\n")
        return resp_json.get("response", "No response from agent server.")
    except Exception as exc:
        print(f"[DEBUG-OPS] HTTP fallback error: {exc}\n")
        logger.error(
            "OpsSupport: Failed to call agent server (%s): %s",
            AGENT_SERVER_URL,
            exc,
        )
        return "Sorry, I am having trouble connecting to the support agent. Please try again later."


# Keep backward-compatible alias used by any hook that calls _call_agent_server
_call_agent_server = _call_agent


async def handle_support_turn(
    thread_id: str,
    user_text: str,
    user_id: str = "",
    user_role: str = "technical",
    user_name: str = "",
    user_email: str = "",
    user_team: str = "",
    user_metadata: dict | None = None,
    case=None,
    conversation_state=None,
    teams_message_id: str = "",
    raw_activity: dict | None = None,
    **kwargs,
) -> str:
    """Thin proxy for custom_hooks.py."""
    print(f"\n[DEBUG-OPS] handle_support_turn called. text='{user_text}' type={type(user_text)}")

    if raw_activity and not isinstance(raw_activity, dict):
        try:
            if hasattr(raw_activity, "from_property"):
                user_id = getattr(raw_activity.from_property, "id", user_id)
            elif hasattr(raw_activity, "from"):
                user_id = getattr(raw_activity, "from", {}).get("id", user_id)
        except Exception:
            pass
    elif isinstance(raw_activity, dict):
        if not user_id:
            from_val: dict[str, object] = raw_activity.get("from") or {}  # type: ignore[assignment]
            user_id = str(from_val.get("id") or "webchat_user")
        if not user_role:
            user_role = str(raw_activity.get("user_type") or "technical")

    user_text = str(user_text) if user_text is not None else "help"

    logger.info("handle_support_turn: session=%r, user=%r", str(thread_id), str(user_id))
    return await _call_agent(
        session_id=str(thread_id),
        user_text=str(user_text),
        user_id=str(user_id) or "webchat_user",
        user_role=str(user_role) or "technical",
        user_name=str(user_name),
        user_email=str(user_email),
        user_team=str(user_team),
        user_metadata=user_metadata,
    )


async def run_ops_support(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
    **kwargs,
) -> dict:
    """Bridge for AI Studio Agent Skill. Aligned with demo.py signature."""
    print(f"\n[DEBUG-OPS] run_ops_support triggered for dialog {dialog_name}")

    # 1. Extract session_id
    session_id = "unknown_session"
    try:
        if hasattr(context, "activity") and hasattr(context.activity, "conversation"):
            session_id = context.activity.conversation.id
    except Exception as exc:
        print(f"[DEBUG-OPS] Session extract failed: {exc}")

    # 2. Extract user_text
    user_text = "help"
    try:
        if hasattr(context, "activity") and hasattr(context.activity, "text"):
            user_text = context.activity.text
        elif hasattr(aistudio_conv_state, "get_conv_input_as_param"):
            user_text = aistudio_conv_state.get_conv_input_as_param("user_text") or "help"
    except Exception as exc:
        print(f"[DEBUG-OPS] Text extract failed: {exc}")

    # 3. Extract user_id / role / name / email
    user_id = "webchat_user"
    user_role = "technical"
    user_name = ""
    user_email = ""
    team_id = ""
    metadata = {}

    try:
        activity = getattr(context, "activity", None)
        if activity:
            # 1. Basic From info
            if hasattr(activity, "from_property") and activity.from_property:
                user_id = activity.from_property.id or user_id
                user_name = activity.from_property.name or ""
            
            # 2. Channel Data (Teams)
            cd = getattr(activity, "channel_data", {}) or {}
            if isinstance(cd, dict):
                metadata["channel_data"] = cd
                user_role = cd.get("user_role", user_role)
                tenant = cd.get("tenant", {}) or {}
                team_id = tenant.get("id") or ""
                
                # Check channelData.user.email
                u = cd.get("user", {}) or {}
                if isinstance(u, dict) and u.get("email"):
                    user_email = u["email"]

            # 3. Entities (Mentions/Metadata)
            entities = getattr(activity, "entities", []) or []
            if entities:
                metadata["entities"] = []
                for ent in entities:
                    e_dict = ent if isinstance(ent, dict) else getattr(ent, "__dict__", {})
                    metadata["entities"].append(e_dict)
                    if not user_email:
                        user_info = e_dict.get("mentioned", {}) or e_dict.get("user", {})
                        if isinstance(user_info, dict) and user_info.get("email"):
                            user_email = user_info["email"]
                        elif e_dict.get("email"):
                            user_email = e_dict["email"]

            # 4. Fallback for email
            if not user_email and "@" in user_id:
                user_email = user_id

    except Exception as exc:
        print(f"[DEBUG-OPS] User detail extract failed: {exc}")

    try:
        from custom.helpers.custom_bot_helper import Custom_Bot_Helper

        teams_details = await Custom_Bot_Helper.store_teams_user(
            context, aistudio_conv_state
        )
        if teams_details:
            user_id = teams_details.get("user_id") or user_id
            user_name = teams_details.get("user_name") or user_name
            user_email = teams_details.get("email") or user_email
            team_id = teams_details.get("tenant_id") or team_id
            metadata["teams_user"] = teams_details
    except Exception as exc:
        logger.warning("ops_support: failed to enrich Teams user details: %s", exc)
    
    # ...existing typing indicator logic...
    try:
        from botbuilder.schema import Activity, ActivityTypes  # type: ignore[import]
        await context.send_activity(Activity(type=ActivityTypes.typing))
    except Exception as _te:
        logger.debug("Typing indicator not sent (non-fatal): %s", _te)

    # 5. Call agent (direct or HTTP fallback)
    resp_text = await _call_agent(
        str(session_id), str(user_text), str(user_id), str(user_role),
        user_name=str(user_name), user_email=str(user_email),
        user_team=str(team_id), user_metadata=metadata
    )

    # 5. Inject back into AI Studio state so ${conv.response} works
    if hasattr(aistudio_conv_state, "add_conv_input_as_param"):
        print("[DEBUG-OPS] Injecting response into conv.response")
        aistudio_conv_state.add_conv_input_as_param("response", resp_text)

    return {"response": resp_text}
