"""
Lazy exports for gateway package symbols.

Avoid importing ``message_gateway`` on package import to reduce
import-order coupling with the agents package.
"""
from __future__ import annotations

from importlib import import_module

__all__ = ["MessageGateway"]


def __getattr__(name: str):
    if name != "MessageGateway":
        raise AttributeError(f"module 'gateway' has no attribute '{name}'")
    module = import_module("gateway.message_gateway")
    value = getattr(module, "MessageGateway")
    globals()[name] = value
    return value
