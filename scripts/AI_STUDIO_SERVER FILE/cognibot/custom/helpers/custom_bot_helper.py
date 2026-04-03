import logging
from typing import Any, Dict, Optional

from asgiref.sync import sync_to_async
from botbuilder.core import TurnContext
from botbuilder.core.teams import TeamsInfo
from django.apps import apps
from django.db import connections

from aistudiobot.aistudio.dialog.state import AIStudioConvState
from aistudiobot.aistudio.utils.constants import Constants

from custom.models import TeamsUserIdentity

logger = logging.getLogger(__name__)


class Custom_Bot_Helper:
    @staticmethod
    async def teams_email(
        context: TurnContext,
        aistudio_conv_state: Optional[Any] = None,
    ) -> str:
        teams_details = await Custom_Bot_Helper.store_teams_user(
            context, aistudio_conv_state
        )
        return teams_details.get("email", "")

    @staticmethod
    async def teams_user(
        context: TurnContext,
        aistudio_conv_state: Optional[Any] = None,
    ) -> str:
        teams_details = await Custom_Bot_Helper.store_teams_user(
            context, aistudio_conv_state
        )
        return teams_details.get("user_name", "")

    @staticmethod
    async def store_teams_user(
        context: TurnContext,
        aistudio_conv_state: Optional[Any] = None,
    ) -> Dict[str, str]:
        teams_details = await Custom_Bot_Helper._extract_teams_details(context)
        if not teams_details:
            return {}

        await Custom_Bot_Helper._apply_to_conv_state(
            context, aistudio_conv_state, teams_details
        )

        try:
            await Custom_Bot_Helper._upsert_teams_user(teams_details)
        except Exception:
            logger.exception(
                "Unable to store Teams user details for conversation_id=%s user_id=%s",
                teams_details.get("conversation_id", ""),
                teams_details.get("user_id", ""),
            )

        return teams_details

    @staticmethod
    async def _extract_teams_details(context: TurnContext) -> Dict[str, str]:
        activity = getattr(context, "activity", None)
        if not activity or getattr(activity, "channel_id", "") != "msteams":
            return {}

        from_property = getattr(activity, "from_property", None)
        conversation = getattr(activity, "conversation", None)
        channel_data = getattr(activity, "channel_data", {}) or {}

        user_id = str(getattr(from_property, "id", "") or "")
        user_name = str(getattr(from_property, "name", "") or "")
        user_email = ""
        tenant_id = ""
        aad_object_id = str(getattr(from_property, "aad_object_id", "") or "")

        if isinstance(channel_data, dict):
            tenant = channel_data.get("tenant", {}) or {}
            user = channel_data.get("user", {}) or {}

            if isinstance(tenant, dict):
                tenant_id = str(tenant.get("id") or "")

            if isinstance(user, dict):
                user_name = str(user.get("name") or user_name)
                user_email = str(user.get("email") or user_email).strip()
                aad_object_id = str(
                    user.get("aadObjectId")
                    or user.get("aad_object_id")
                    or aad_object_id
                )

        member = await Custom_Bot_Helper._get_teams_member(context, user_id)
        if member is not None:
            user_id = str(getattr(member, "id", "") or user_id)
            user_name = str(getattr(member, "name", "") or user_name)
            user_email = str(
                getattr(member, "email", "")
                or getattr(member, "user_principal_name", "")
                or user_email
            ).strip()
            aad_object_id = str(
                getattr(member, "aad_object_id", "") or aad_object_id
            )

        conversation_id = str(getattr(conversation, "id", "") or "")
        if not conversation_id or not (user_id or user_email):
            return {}

        return {
            "conversation_id": conversation_id,
            "channel_id": str(getattr(activity, "channel_id", "") or "msteams"),
            "user_id": user_id or user_email,
            "user_name": user_name,
            "email": user_email,
            "tenant_id": tenant_id,
            "aad_object_id": aad_object_id,
        }

    @staticmethod
    async def _get_teams_member(
        context: TurnContext, user_id: str
    ) -> Optional[Any]:
        if user_id:
            try:
                return await TeamsInfo.get_member(context, user_id)
            except Exception:
                logger.debug("TeamsInfo.get_member failed for user_id=%s", user_id)

        try:
            team_members = await TeamsInfo.get_members(context)
        except Exception:
            logger.exception("TeamsInfo.get_members failed while extracting email")
            return None

        for member in team_members:
            if str(getattr(member, "id", "") or "") == user_id:
                return member

        return team_members[0] if team_members else None

    @staticmethod
    async def _apply_to_conv_state(
        context: TurnContext,
        aistudio_conv_state: Optional[Any],
        teams_details: Dict[str, str],
    ) -> None:
        resolved_state = await Custom_Bot_Helper._resolve_conv_state(
            context, aistudio_conv_state
        )
        if not resolved_state or not hasattr(
            resolved_state, "add_conv_input_as_param"
        ):
            return

        for key, value in (
            ("Team_EmailId", teams_details.get("email", "")),
            ("Team_User", teams_details.get("user_name", "")),
            ("Team_UserId", teams_details.get("user_id", "")),
            ("Team_TenantId", teams_details.get("tenant_id", "")),
            ("Team_AadObjectId", teams_details.get("aad_object_id", "")),
        ):
            if value:
                resolved_state.add_conv_input_as_param(key, value)

    @staticmethod
    async def _resolve_conv_state(
        context: TurnContext, aistudio_conv_state: Optional[Any]
    ) -> Optional[Any]:
        if aistudio_conv_state and hasattr(
            aistudio_conv_state, "add_conv_input_as_param"
        ):
            return aistudio_conv_state

        conv_state = aistudio_conv_state
        if conv_state is None:
            try:
                bot_app = apps.get_app_config(Constants.AISTUDIOBOT)
                conv_state = bot_app.conv_state
            except Exception:
                logger.debug("Unable to resolve bot conversation state from app config")
                return None

        try:
            return await AIStudioConvState.get(conv_state, context)
        except Exception:
            logger.debug("Unable to resolve AIStudio conversation state")
            return None

    @staticmethod
    def _get_db_alias() -> str:
        if "custom" in connections.databases:
            return "custom"
        if "default" in connections.databases:
            return "default"
        return next(iter(connections.databases.keys()))

    @staticmethod
    def _ensure_teams_user_table(db_alias: str) -> None:
        connection = connections[db_alias]
        table_name = TeamsUserIdentity._meta.db_table
        existing_tables = connection.introspection.table_names()
        if table_name in existing_tables:
            return

        logger.warning(
            "Teams user table %s is missing in database alias %s. Creating it now.",
            table_name,
            db_alias,
        )
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(TeamsUserIdentity)

    @staticmethod
    @sync_to_async
    def _upsert_teams_user(teams_details: Dict[str, str]) -> None:
        lookup_user_id = teams_details.get("user_id") or teams_details.get("email", "")
        if not teams_details.get("conversation_id") or not lookup_user_id:
            return

        db_alias = Custom_Bot_Helper._get_db_alias()
        Custom_Bot_Helper._ensure_teams_user_table(db_alias)
        TeamsUserIdentity.objects.using(db_alias).update_or_create(
            conversation_id=teams_details["conversation_id"],
            user_id=lookup_user_id,
            defaults={
                "channel_id": teams_details.get("channel_id", "msteams"),
                "user_name": teams_details.get("user_name", ""),
                "email": teams_details.get("email", ""),
                "tenant_id": teams_details.get("tenant_id", ""),
                "aad_object_id": teams_details.get("aad_object_id", ""),
            },
        )
