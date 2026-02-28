from django.apps import AppConfig

try:
    from aistudiobot.aistudio.utils.constants import Constants
    _CUSTOM_NAME = Constants.CUSTOM
except ImportError:
    _CUSTOM_NAME = "custom"


class CustomAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = _CUSTOM_NAME
    verbose_name = "AE Agentic Support Extension"
