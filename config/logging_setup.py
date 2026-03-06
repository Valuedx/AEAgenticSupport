"""
Logging configuration — application and audit loggers.
"""

import os
import logging
from logging.handlers import RotatingFileHandler

from config.settings import CONFIG


import json
from datetime import datetime

import re

class JsonFormatter(logging.Formatter):
    """Structured JSON formatter with PII masking."""
    
    PII_PATTERNS = {
        "email": r"[\w\.-]+@[\w\.-]+\.\w+",
        "ip": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "secret": r"(?i)(password|passwd|secret|token|key|api_key|apikey)[\s=:]+[\"']?([^\s\"']+)[\"']?"
    }

    def _mask_pii(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        
        # Mask emails
        text = re.sub(self.PII_PATTERNS["email"], "[EMAIL_REDACTED]", text)
        # Mask IPs
        text = re.sub(self.PII_PATTERNS["ip"], "[IP_REDACTED]", text)
        # Mask secrets (keep key, mask value)
        text = re.sub(self.PII_PATTERNS["secret"], r"\1: [REDACTED]", text)
        
        return text

    def format(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self._mask_pii(record.getMessage()),
            "module": record.module,
            "func": record.funcName,
        }
        if hasattr(record, "conversation_id"):
            log_entry["conversation_id"] = record.conversation_id
        if record.exc_info:
            log_entry["exception"] = self._mask_pii(self.formatException(record.exc_info))
        return json.dumps(log_entry)

def setup_logging():
    """
    Initialise and return (app_logger, audit_logger).
    Logs go to both console and rotating files under LOG_DIR.
    """
    log_dir = CONFIG["LOG_DIR"]
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, CONFIG["LOG_LEVEL"].upper(), logging.INFO)
    
    # Standard format
    std_formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # JSON format for files
    json_formatter = JsonFormatter()
    
    use_json = CONFIG.get("LOG_JSON", True)
    file_formatter = json_formatter if use_json else std_formatter

    # ── Application logger ──
    app_logger = logging.getLogger("ops_agent")
    app_logger.setLevel(log_level)

    console = logging.StreamHandler()
    console.setFormatter(std_formatter)
    app_logger.addHandler(console)

    app_file = RotatingFileHandler(
        os.path.join(log_dir, "ops_agent.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    app_file.setFormatter(file_formatter)
    app_logger.addHandler(app_file)

    # ── Audit logger (separate file, always INFO+) ──
    audit_logger = logging.getLogger("ops_agent.audit")
    audit_logger.setLevel(logging.INFO)

    audit_file = RotatingFileHandler(
        os.path.join(log_dir, "audit.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
    )
    audit_file.setFormatter(file_formatter)
    audit_logger.addHandler(audit_file)

    return app_logger, audit_logger
