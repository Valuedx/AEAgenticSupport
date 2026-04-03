from botbuilder.core import TurnContext

from aistudiobot.aistudio.dialog.state import (
    AIStudioConvState,
    AIStudioUserState,
)
from aistudiobot.aistudio.utils.common import CommonUtils


# This shows how to access and set AIStudioDialogState
async def compute_product(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    n1 = float(aistudio_conv_state.get_dialog_input_as_param(dialog_name, "n1") or 1)
    n2 = float(aistudio_conv_state.get_dialog_input_as_param(dialog_name, "n2") or 1)
    aistudio_conv_state.add_dialog_input_as_param(dialog_name, "product", str(n1 * n2))


# This shows how to access and set AIStudioUserState
async def set_greeting(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    lang = aistudio_user_state.get_user_input_as_param("lang") or "English"
    greeting = {
        "English": "Hello, how are you",
        "German": "Hallo, Wie geht es?",
        "French": "Bonjour, Comment ca va?",
        "Portuguese": "Olá, como vai?",
    }
    aistudio_user_state.add_user_input_as_param("greeting", greeting.get(lang))


async def remove_greeting(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    aistudio_user_state.pop_user_input_as_param("greeting")


# This shows how to access and set AIStudioConvState
async def set_mood_reply(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    mood = aistudio_conv_state.get_conv_input_as_param("mood") or "Good"
    reply = {
        "Good": "I am glad I was able to help you",
        "Bad": "I will try to serve you better",
    }
    aistudio_conv_state.add_conv_input_as_param("reply", reply[mood])


# This method shows how to get results of an utterance through an NLU configured with the current skill of user.
async def get_nlu_response(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    from aistudio_cognition.cognibot.models import State
    from django.apps import apps

    from aistudiobot.aistudio.utils.constants import Constants
    from aistudiobot.helpers.bot_helper import BotHelper

    # Utterance that is sent to NLU
    utterance = "I would like to raise a ticket"
    # Which State to save the results in
    nlu_response_state = State.CONVERSATION
    # Which ID to save the results under
    nlu_response_id = "nlu_resp"
    # For this specific example it will store the results in "${conv.nlu_resp}"

    bot_app = apps.get_app_config(Constants.AISTUDIOBOT)
    cognibot = bot_app.cognibot
    skill_name = aistudio_conv_state.get_conv_input_as_param(Constants.SKILL)
    skill_settings = cognibot.skill_settings_map.get(skill_name)
    # Stores the results inside ${conv.nlu_resp}
    # eg- Entities will be available in ${conv.nlu_resp.__entities__}
    # Returns None if it failed to get the nlu_response. Returns intent if it successfully gets the nlu_response.
    intent = await BotHelper.nlu_call(
        skill_settings,
        cognibot.nlu_settings_list,
        utterance,
        aistudio_conv_state,
        aistudio_user_state,
        nlu_response_state,
        nlu_response_id,
        context,
    )


# This shows how to use AIStudio Credential
async def verify_credential(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
):
    credential = CommonUtils.get_credential("Demo")
    if credential:
        if credential.value1 == "demo":
            result = "Credential {Demo} verification successful"
        else:
            result = "Credential {Demo} verification failed"
    else:
        result = "Credential {Demo} not found"

    # Setting result to dialog state
    aistudio_conv_state.add_dialog_input_as_param(
        dialog_name, "credential_result", result
    )
