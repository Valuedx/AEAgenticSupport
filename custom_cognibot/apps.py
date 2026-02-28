import logging

from django.apps import AppConfig
from aistudiobot.aistudio.utils.constants import Constants

logger = logging.getLogger(__name__)


class CustomAppConfig(AppConfig):
    name = Constants.CUSTOM
