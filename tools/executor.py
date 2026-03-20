"""
Runtime execution helpers for hydrated tool handlers.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from config.llm_client import get_current_trace
from config.observability import span_context
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
        logged_kwargs = self.sanitize_logged_params(kwargs)
        self._audit.info("TOOL_CALL tool=%s params=%s", tool_name, logged_kwargs)

        trace = get_current_trace()
        with span_context(trace, f"tool:{tool_name}", as_type="tool", input=logged_kwargs) as span:
            t0 = time.perf_counter()
            try:
                result = handler(**kwargs)

                if isinstance(result, ToolResult):
                    if result.tool_name == "":
                        result.tool_name = tool_name
                    if result.success:
                        self._audit.info("TOOL_OK tool=%s", tool_name)
                    else:
                        self._audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, result.error)
                    self._log_interaction(tool_name, logged_kwargs, result.success, result.error)
                    span.update(
                        output={"success": result.success, "error": result.error},
                        metadata={"latency_ms": (time.perf_counter() - t0) * 1000},
                    )
                    return result

                if isinstance(result, dict) and isinstance(result.get("success"), bool):
                    if result["success"]:
                        self._audit.info("TOOL_OK tool=%s", tool_name)
                        self._log_interaction(tool_name, logged_kwargs, True, "")
                        span.update(
                            output={"success": True},
                            metadata={"latency_ms": (time.perf_counter() - t0) * 1000},
                        )
                        return ToolResult(success=True, data=result, tool_name=tool_name)
                    error = str(
                        result.get("error")
                        or f"Tool '{tool_name}' reported unsuccessful execution."
                    )
                    self._audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, error)
                    self._log_interaction(tool_name, logged_kwargs, False, error)
                    span.update(
                        output={"success": False, "error": error},
                        metadata={"latency_ms": (time.perf_counter() - t0) * 1000},
                    )
                    return ToolResult(
                        success=False,
                        data=result,
                        error=error,
                        tool_name=tool_name,
                    )

                self._audit.info("TOOL_OK tool=%s", tool_name)
                self._log_interaction(tool_name, logged_kwargs, True, "")
                span.update(
                    output={"success": True},
                    metadata={"latency_ms": (time.perf_counter() - t0) * 1000},
                )
                return ToolResult(success=True, data=result, tool_name=tool_name)
            except Exception as exc:
                self._logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
                self._audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, exc)
                self._log_interaction(tool_name, logged_kwargs, False, str(exc))
                span.update(
                    output={"success": False, "error": str(exc)[:500]},
                    metadata={"latency_ms": (time.perf_counter() - t0) * 1000},
                )
                return ToolResult(success=False, error=str(exc), tool_name=tool_name)

    def _log_interaction(self, tool_name: str, params: dict, success: bool, error: str):
        if not self._interaction_logger:
            return
        try:
            self._interaction_logger(tool_name, params, success, error)
        except Exception:
            self._logger.debug("Skipping tool interaction log for %s", tool_name)
