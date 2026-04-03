import logging

from botbuilder.dialogs import Dialog
from django.http import HttpResponse

from aistudiobot.hooks import ChatbotHooks
from custom.helpers.custom_bot_helper import Custom_Bot_Helper

# All logs will be available in logs/custom.log
logger = logging.getLogger(__name__)


class CustomChatbotHooks(ChatbotHooks):
    # Add all custom dialog objects to export_dialogs list. Only the dialogs in the list will be registered during execution.
    export_dialogs = []

    async def root_dialog_hook(conv_state, user_state, turn_context) -> Dialog:
        """This method will be invoked before matching triggers from our chatbot.
        Can be used to invoke custom dialogs by return a dialog object that is also available in global var export_dialogs

        Args:
            conv_state (botbuilder.core.ConversationState)
            user_state (botbuilder.core.UserState)
            turn_context (botbuilder.core.TurnContext)

        Returns:
            botbuilder.dialogs.Dialog : Dialog to be invoked, return None if not invoking dialogs.
        """
        logger.info("Root dialog hook called")
        return None

    async def storecon_hook(turn_context):
        """This method will be invoked for every activity before user chat history is logged

        Args:
            turn_context (botbuilder.core.TurnContext)
        """
        logger.info("Storecon hook called")
        try:
            await Custom_Bot_Helper.store_teams_user(turn_context)
        except Exception:
            logger.exception("Unable to persist Teams user details in storecon_hook")
        return None

    async def custom_view_hook(request) -> HttpResponse:
        """This method will be invoked when api/custom REST API is called from any source.

        Args:
            request : Request object sent while invoking the REST API

        Returns:
            HttpResponse : The http reponse for the REST API invocation, return HttpResponse 400 if not implemented
        """
        logger.info("Custom view hook called")
        return HttpResponse(status=400)

    async def webchat_join_event_hook(conv_state, user_state, turn_context):
        """This method will be invoked when user starts a new conversation

        Args:
            conv_state (botbuilder.core.ConversationState)
            user_state (botbuilder.core.UserState)
            turn_context (botbuilder.core.TurnContext)
        """
        logger.info("Webchat join hook called")
        return None

    async def aistudio_dialog_element_hook(conv_state, user_state, turn_context):
        """This method is invoked before executing any dialog element

        Args:
            conv_state (botbuilder.core.ConversationState)
            user_state (botbuilder.core.UserState)
            turn_context (botbuilder.core.TurnContext)
        """
        logger.info("AIStudio dialog hook called")
        try:
            await Custom_Bot_Helper.store_teams_user(turn_context, conv_state)
        except Exception:
            logger.exception(
                "Unable to populate Teams user details in aistudio_dialog_element_hook"
            )
        return None

    async def api_messages_hook(request, activity):
        """This method is invoked for every api/messages REST API invocation

        Args:
            request : Request object sent while invoking the REST API
            activity : Activity object
        """
        logger.info("api messages hook called")
        return None

    async def api_reply_hook(request, body):
        """This method is invoked for every api/reply REST API invocation

        Args:
            request : Request object sent while invoking the REST API
            body : JSON string body with details of the workflow response
        """
        logger.info("api reply hook called")
        return None

    async def cancel_conv_hook(conv_state, user_state, turn_context):
        """This method is invoked when a conversation is cancelled

        Args:
            conv_state (botbuilder.core.ConversationState)
            user_state (botbuilder.core.UserState)
            turn_context (botbuilder.core.TurnContext)
        """
        logger.info("Cancel conversation hook called")
        return None

    async def voice_bot_start_conv_hook(request, file_data):
        """This method is invoked before sending data to start a voice call.

        Args:
            request : Request object sent while invoking the REST API
            file_data : CSV file (; separator) contents, i.e. phone numbers and required details

        Return:
            file_data : Modified file data to be sent while making the voice call
        """
        return file_data

    async def voice_init_conv_hook(conversation_id, body):
        """This method is invoked when a conversation is initiated for Voice

        Args:
            request : Request object sent while invoking the REST API
            body : JSON string body with details of the workflow response
        """
        logger.info("Voice init conversation hook called")
        return None

    async def voice_end_conv_hook(conversation_id, request=None, activity=None):
        """This method is invoked when the user disconnects from an ongoing voice call
           or the voice_conversation execution has timed out

        Args:
            conversation_id : The ending conversation's conversation id
            request : Request object sent while invoking the REST API
            activity : The activity object,
                       sent only when the voice_conversation is timed out
                       else it is None
        """
        return None

    async def sms_bot_start_conv_hook(body):
        """This method is invoked before a conversation is initiated for SMS
           Can be used to implement logic that would choose source (bot) numbers to send the SMS

        Args:
            body : JSON body with details of the dialog to be triggered
        """
        logger.info("SMS bot start conversation hook called")
        return None

    async def sms_bot_reply_hook(
        request, conversation_id, activity_id, end_conversation, response_list
    ):
        """This method is invoked after the SMS has been sent a list of responses

        Args:
            request: Acitivity object
            conversation_id: The conversation ID
            activity_id:
            end_conversation: Boolean flag to mark end of SMS conversation
                              (Set from the dialog designer - conversation state)
            response_list: A dictonary with key : message number and value : HttpResponse
        """
        logger.info("SMS bot reply hook called")
        return None

    async def whatsapp_data_channel(flow_data):
        """This method is invoked inside whatsapp/data-channel view. This is used by
           WhatsApp flows using data-exchange button to fetch data dynamically from
           the data-channel during a flow.

        Args:
            flow_data (dict): data sent by the flow in the request
                format: {
                    "version": "3.0",
                    "action": "data_exchange",
                    "screen": "SCREEN_NAME",
                    "data": {
                        "key1": "value1",
                        "key2": "value2"
                    },
                    "flow_token": "UNIQUE_FLOW_TOKEN"
                }

        Returns:
            dict: response_data that you want to return in the data-channel response
                format: {
                    "version": "3.0",
                    "screen": "SCREEN_NAME",
                    "data": {
                        "key1": "value1",
                        "key2": "value2"
                    },
                }
        """
        logger.info(f"WhatsApp Flows Request data - {flow_data}")

        if flow_data.get("action") == "ping":
            response_data = {"version": "3.0", "data": {"status": "active"}}

        logger.info(f"WhatsApp Flows Response data - {response_data}")
        return response_data

    async def custom_schedules():
        """This method allows to add any custom schedules to chatbot-webservice.

        Returns:
            List of Schedule objects to be added
        """
        return None
