class AIStudioHelper:
    @staticmethod
    async def begin_aistudio_dialog(
        aistudio_conv_state, dialog_name, step_context, branched=False
    ):
        from aistudiobot.helpers.bot_helper import BotHelper

        return BotHelper.begin_aistudio_dialog(
            aistudio_conv_state, dialog_name, step_context, branched
        )
