import json

import logging

from botbuilder.core import TurnContext

from botbuilder.core.teams import TeamsInfo

from aistudiobot.aistudio.utils.constants import Constants

from django.apps import apps





from aistudiobot.aistudio.dialog.state import (

    AIStudioConvState,

)

logger = logging.getLogger(__name__)



class Custom_Bot_Helper:

    @staticmethod

    async def teams_email(

        context: TurnContext,

        aistudio_conv_state: AIStudioConvState,

    ):



        bot_app = apps.get_app_config(Constants.AISTUDIOBOT)

        conv_state = bot_app.conv_state

        aistudio_conv_state = await AIStudioConvState.get(conv_state, context)



        if context.activity.channel_id == 'msteams':

            team_members = await TeamsInfo.get_members(context)

            for member in team_members:

                    conversation_reference = context.get_conversation_reference(

                        context.activity

                    )

            team_email = getattr(member,"email")

            aistudio_conv_state.add_conv_input_as_param("Team_EmailId", team_email)

            #aistudio_user_state.add_user_input_as_param("Team_EmailId", team_email)



    @staticmethod

    async def teams_user(

    context: TurnContext,

    aistudio_conv_state: AIStudioConvState,

    ):

        bot_app = apps.get_app_config(Constants.AISTUDIOBOT)

        conv_state = bot_app.conv_state

        aistudio_conv_state = await AIStudioConvState.get(conv_state, context)



        if context.activity.channel_id == 'msteams':

            team_members = await TeamsInfo.get_members(context)

            for member in team_members:

                    conversation_reference = context.get_conversation_reference(

                        context.activity

                    )

            team_user = getattr(member,"name")

            aistudio_conv_state.add_conv_input_as_param("Team_User", team_user)