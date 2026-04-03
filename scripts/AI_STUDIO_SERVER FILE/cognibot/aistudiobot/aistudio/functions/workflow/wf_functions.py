import json

from botbuilder.core import ConversationState, TurnContext
from django.conf import settings

from aistudiobot.aistudio.dialog.state import (
    AIStudioConvState,
    AIStudioUserState,
)
from aistudiobot.helpers.db_helper import DBHelper


async def additional_info(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    turn_context = context
    service_url = (
        turn_context.activity.service_url
        + "/v3/conversations/"
        + turn_context.activity.conversation.id
        + "/activities/reply"
    )
    conversation_details = {
        "chat_channel": turn_context.activity.channel_id,
        "service_url": service_url,
        "bot_id": turn_context.activity.recipient.id,
        "bot_name": turn_context.activity.recipient.name,
        "user_id": turn_context.activity.from_property.id,
        "user_name": turn_context.activity.from_property.name,
        "conversation_id": turn_context.activity.conversation.id,
        "model_conversation_id": await DBHelper.get_current_model_conv_id(
            turn_context.activity.conversation.id
        ),
    }
    additional_info = {}
    additional_info["response_type"] = "chatbot"
    additional_info["conversation_details"] = conversation_details
    additional_info["auth_header"] = context.activity.additional_properties[
        "auth_header"
    ]
    if claims := context.activity.additional_properties.get("claims"):
        additional_info["claims"] = claims

    return json.dumps(additional_info)


async def chatbot_URL(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    return settings.CHATBOT_URL


async def notification_URL(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    return settings.CHATBOT_URL.replace("reply", "push-notifications")
