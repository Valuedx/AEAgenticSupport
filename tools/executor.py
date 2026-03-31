"""
Runtime execution helpers for hydrated tool handlers.
"""
from __future__ import annotations

import logging
from typing import Callable

from tools.base import ToolResult


class ToolExecutor:
    """Execute hydrated tool handlers with consistent logging and normalization."""

    def __init__(
        self,
        *,
        app_logger: logging.Logger | None = None,
        audit_logger: logging.Logger | None = None,
        interaction_logger: Callable[[str, dict, bool, str], None] | None = None,
    ):
        self._logger = app_logger or logging.getLogger("ops_agent.tools.executor")
        self._audit = audit_logger or logging.getLogger("ops_agent.audit")
        self._interaction_logger = interaction_logger

    @staticmethod
    def sanitize_logged_params(kwargs: dict) -> dict:
        return {
            key: value
            for key, value in (kwargs or {}).items()
            if not str(key).startswith("_")
        }

    def execute(self, tool_name: str, handler: Callable, kwargs: dict) -> ToolResult:
        import asyncio
        import inspect

        logged_kwargs = self.sanitize_logged_params(kwargs)
        self._audit.info("TOOL_CALL tool=%s params=%s", tool_name, logged_kwargs)
        try:
            # ── Execute the handler ──────────────────────────────────────────
            # Our tool handlers can be synchronous or asynchronous.
            result = handler(**kwargs)

            # ── Async Bridge: Handle coroutines from 'async def' functions ───
            if inspect.iscoroutine(result):
                try:
                    # In a typical server (Flask/Threaded), we likely don't have
                    # a running loop in this worker thread.
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    # No loop in this thread; create one.
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                if loop.is_running():
                    # This happens if we are already inside an async context.
                    # We use a thread-safe way to run the coroutine or nest_asyncio.
                    # For this environment, since it's mosty sync, we'll try to use 
                    # a nested loop if possible, or just a wrapper.
                    try:
                        import nest_asyncio
                        nest_asyncio.apply()
                        result = loop.run_until_complete(result)
                    except (ImportError, RuntimeError):
                        # Fallback for complex environments
                        self._logger.warning("Already in a running loop for %s; execution might be deferred.", tool_name)
                        # In the worst case, we might need a concurrent.futures.Future bridge.
                        # But for our current architecture, nest_asyncio is standard.
                else:
                    result = loop.run_until_complete(result)

            # ── Normalize Result ─────────────────────────────────────────────
            if isinstance(result, ToolResult):
                if result.tool_name == "":
                    result.tool_name = tool_name
                if result.success:
                    self._audit.info("TOOL_OK tool=%s", tool_name)
                else:
                    self._audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, result.error)
                self._log_interaction(tool_name, logged_kwargs, result.success, result.error)
                return result

            if isinstance(result, dict) and isinstance(result.get("success"), bool):
                if result["success"]:
                    self._audit.info("TOOL_OK tool=%s", tool_name)
                    self._log_interaction(tool_name, logged_kwargs, True, "")
                    return ToolResult(success=True, data=result, tool_name=tool_name)
                error = str(
                    result.get("error")
                    or f"Tool '{tool_name}' reported unsuccessful execution."
                )
                self._audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, error)
                self._log_interaction(tool_name, logged_kwargs, False, error)
                # Ensure the full tool result (including error, sop, etc.) is in 'data'
                return ToolResult(
                    success=False,
                    data=result,
                    error=error,
                    tool_name=tool_name,
                )

            self._audit.info("TOOL_OK tool=%s", tool_name)
            self._log_interaction(tool_name, logged_kwargs, True, "")
            return ToolResult(success=True, data=result, tool_name=tool_name)
        except Exception as exc:
            self._logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
            self._audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, exc)
            self._log_interaction(tool_name, logged_kwargs, False, str(exc))
            return ToolResult(success=False, error=str(exc), tool_name=tool_name)

    def _log_interaction(self, tool_name: str, params: dict, success: bool, error: str):
        if not self._interaction_logger:
            return
        try:
            self._interaction_logger(tool_name, params, success, error)
        except Exception:
            self._logger.debug("Skipping tool interaction log for %s", tool_name)
