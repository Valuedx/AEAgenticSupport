"""
LangFuse observability integration.

Provides a lazy-initialized LangFuse client and helper utilities for
creating traces and spans across the agent pipeline.  All LangFuse
operations are guarded so that a missing or misconfigured LangFuse
instance never breaks the core agent flow.

Enable by setting the following environment variables:
    LANGFUSE_ENABLED=true
    LANGFUSE_PUBLIC_KEY=pk-...
    LANGFUSE_SECRET_KEY=sk-...
    LANGFUSE_HOST=http://localhost:3000   (self-hosted) or https://cloud.langfuse.com

See docs/SETUP_GUIDE.md §15 for full configuration details.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

logger = logging.getLogger("ops_agent.observability")

_langfuse_client = None
_langfuse_available: Optional[bool] = None


def _is_enabled() -> bool:
    return os.environ.get("LANGFUSE_ENABLED", "false").lower() in ("1", "true", "yes")


def get_langfuse():
    """Return the singleton LangFuse client, or None if disabled / unavailable."""
    global _langfuse_client, _langfuse_available

    if _langfuse_available is False:
        return None

    if _langfuse_client is not None:
        return _langfuse_client

    if not _is_enabled():
        _langfuse_available = False
        logger.info("LangFuse observability is disabled (LANGFUSE_ENABLED != true)")
        return None

    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse()
        _langfuse_available = True
        logger.info("LangFuse observability initialized (host=%s)", os.environ.get("LANGFUSE_HOST", "default"))
        return _langfuse_client
    except Exception as exc:
        _langfuse_available = False
        logger.warning("LangFuse initialization failed — observability disabled: %s", exc)
        return None


def flush_langfuse() -> None:
    """Flush any buffered LangFuse events.  Safe to call even when disabled."""
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
        except Exception:
            pass


def shutdown_langfuse() -> None:
    """Gracefully shut down the LangFuse client."""
    global _langfuse_client, _langfuse_available
    if _langfuse_client is not None:
        try:
            _langfuse_client.shutdown()
        except Exception:
            pass
        _langfuse_client = None
        _langfuse_available = None


# ---------------------------------------------------------------------------
# Trace / span helpers
# ---------------------------------------------------------------------------

@contextmanager
def trace_context(
    name: str,
    *,
    session_id: str = "",
    user_id: str = "",
    input: Any = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> Generator:
    """Context manager that creates a LangFuse trace.

    Yields a ``trace`` object (or a no-op stub when LangFuse is disabled)
    so callers can create child spans / generations inside the block.
    The trace is automatically ended on exit.

    Usage::

        with trace_context("orchestrator_turn", session_id=cid) as trace:
            # ... agent logic ...
            trace.update(output={"response": "..."})
    """
    lf = get_langfuse()
    if lf is None:
        yield _NoOpTrace()
        return

    trace = None
    try:
        kwargs: dict[str, Any] = {"name": name}
        if session_id:
            kwargs["session_id"] = session_id
        if user_id:
            kwargs["user_id"] = user_id
        if input is not None:
            kwargs["input"] = input
        if metadata:
            kwargs["metadata"] = metadata
        if tags:
            kwargs["tags"] = tags
        trace = lf.trace(**kwargs)
        yield trace
    except Exception as exc:
        logger.debug("LangFuse trace_context error: %s", exc)
        yield _NoOpTrace()
    finally:
        if trace is not None:
            try:
                lf.flush()
            except Exception:
                pass


@contextmanager
def span_context(
    trace: Any,
    name: str,
    *,
    input: Any = None,
    metadata: dict | None = None,
) -> Generator:
    """Context manager that creates a child span on *trace*.

    Usage::

        with span_context(trace, "rag_search", input={"query": q}) as span:
            results = rag.search(...)
            span.update(output=results)
    """
    if isinstance(trace, _NoOpTrace):
        yield _NoOpSpan()
        return

    span = None
    try:
        kwargs: dict[str, Any] = {"name": name}
        if input is not None:
            kwargs["input"] = input
        if metadata:
            kwargs["metadata"] = metadata
        span = trace.span(**kwargs)
        yield span
    except Exception as exc:
        logger.debug("LangFuse span_context error: %s", exc)
        yield _NoOpSpan()
    finally:
        if span is not None:
            try:
                span.end()
            except Exception:
                pass


def create_generation(
    trace: Any,
    *,
    name: str,
    model: str = "",
    input: Any = None,
    output: Any = None,
    usage: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an LLM generation (non-blocking, fire-and-forget)."""
    if isinstance(trace, _NoOpTrace):
        return
    try:
        kwargs: dict[str, Any] = {"name": name}
        if model:
            kwargs["model"] = model
        if input is not None:
            kwargs["input"] = input
        if output is not None:
            kwargs["output"] = output
        if usage:
            kwargs["usage"] = usage
        if metadata:
            kwargs["metadata"] = metadata
        trace.generation(**kwargs)
    except Exception as exc:
        logger.debug("LangFuse create_generation error: %s", exc)


# ---------------------------------------------------------------------------
# No-op stubs — used when LangFuse is disabled so callers don't need
# to sprinkle ``if trace:`` checks everywhere.
# ---------------------------------------------------------------------------

class _NoOpTrace:
    """Drop-in stub returned when LangFuse is disabled."""

    def span(self, **_kw):
        return _NoOpSpan()

    def generation(self, **_kw):
        return None

    def update(self, **_kw):
        return self

    def event(self, **_kw):
        return None


class _NoOpSpan:
    """Drop-in stub for spans when LangFuse is disabled."""

    def update(self, **_kw):
        return self

    def end(self, **_kw):
        return None

    def span(self, **_kw):
        return _NoOpSpan()

    def generation(self, **_kw):
        return None

    def event(self, **_kw):
        return None
