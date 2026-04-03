from botbuilder.core import TurnContext

from aistudiobot.aistudio.dialog.state import (
    AIStudioConvState,
    AIStudioUserState,
)
from aistudio_cognition.cognibot.models import DialogDesigner, Skill
from aistudiobot.aistudio.utils.constants import Constants


async def default_trigger(
    context: TurnContext,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
    skill_settings: Skill,
    dialog_designer: DialogDesigner,
):
    # This is a default method which returns no dialog id i.e. None
    return None


async def nlu_trigger(
    context: TurnContext,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
    skill_settings: Skill,
    dialog_designer: DialogDesigner,
):
    # This is an example custom trigger function that uses NLU response to determine which dialog to be triggered.
    # Examples of how intent, utterance, username and NLU response can be fetched from the converstation state is also shown.
    # If the entities returned contains conference_room_name and Software name, return dialog id conference_or_software,
    # else it will not return any dialog id i.e. None
    # This dialog id will be triggered further from the dialog designer.

    conv_params = aistudio_conv_state.get_conv_params()
    intent = conv_params[Constants.INTENT]
    utterance = conv_params[Constants.UTTERANCE]
    username = conv_params[Constants.USERNAME]
    nlu_response = conv_params[Constants.NLU_RESPONSE]
    entity_names = [x["name"] for x in nlu_response.get("entities") if "name" in x]

    # Do something with nlu results
    if "ConferenceRoomName" in entity_names and "Software Name" in entity_names:
        # Starts dialog with name "conference_or_software"
        return "conference_or_software"
    return None
