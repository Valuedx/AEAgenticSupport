"""
Logging configuration — application and audit loggers.
"""

import os
import logging
from logging.handlers import RotatingFileHandler

from config.settings import CONFIG


def setup_logging():
    """
    Initialise and return (app_logger, audit_logger).
    Logs go to both console and rotating files under LOG_DIR.
    """
    log_dir = CONFIG["LOG_DIR"]
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, CONFIG["LOG_LEVEL"].upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Application logger ──
    app_logger = logging.getLogger("ops_agent")
    app_logger.setLevel(log_level)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    app_logger.addHandler(console)

    app_file = RotatingFileHandler(
        os.path.join(log_dir, "ops_agent.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    app_file.setFormatter(formatter)
    app_logger.addHandler(app_file)

    # ── Audit logger (separate file, always INFO+) ──
    audit_logger = logging.getLogger("ops_agent.audit")
    audit_logger.setLevel(logging.INFO)

    audit_file = RotatingFileHandler(
        os.path.join(log_dir, "audit.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
    )
    audit_file.setFormatter(formatter)
    audit_logger.addHandler(audit_file)

    return app_logger, audit_logger
