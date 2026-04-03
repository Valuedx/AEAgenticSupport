import logging
import sys

from django.apps import AppConfig
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError
from aistudiobot.aistudio.utils.constants import Constants

logger = logging.getLogger(__name__)


class CustomAppConfig(AppConfig):
    # DO NOT CHANGE OR REMOVE THE BELOW LINE. This would result in custom code not being registered.
    name = Constants.CUSTOM
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        if len(sys.argv) > 1 and sys.argv[1] in {
            "makemigrations",
            "migrate",
            "showmigrations",
            "collectstatic",
        }:
            return

        try:
            from custom.models import TeamsUserIdentity

            db_alias = "custom" if "custom" in connections.databases else "default"
            connection = connections[db_alias]
            table_name = TeamsUserIdentity._meta.db_table

            if table_name in connection.introspection.table_names():
                return

            logger.warning(
                "Teams user table %s is missing in database alias %s. Creating it during app startup.",
                table_name,
                db_alias,
            )
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(TeamsUserIdentity)
        except (OperationalError, ProgrammingError):
            logger.exception("Unable to validate Teams user table during app startup")
