"""
LangFuse observability integration (v4 / OpenTelemetry-based API).

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
        logger.info(
            "LangFuse observability initialized (host=%s)",
            os.environ.get("LANGFUSE_HOST", "default"),
        )
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
# Trace / span helpers  (LangFuse v4 — OpenTelemetry context-based)
#
# In v4 nesting is automatic: the outermost ``start_as_current_observation``
# becomes the root trace, and any inner ``start_as_current_observation`` calls
# (even in different functions) become child spans of the active context.
# ``propagate_attributes`` injects session_id / user_id / tags into the
# active OTel context so all observations within the block inherit them.
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
    """Context manager that creates a LangFuse root trace (v4 API).

    Yields a span object (or a no-op stub when LangFuse is disabled)
    so callers can call ``span.update(output=...)`` at the end.

    Usage::

        with trace_context("orchestrator_turn", session_id=cid) as trace:
            # ... agent logic (child spans auto-nest) ...
            trace.update(output={"response": "..."})
    """
    lf = get_langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    try:
        from langfuse import propagate_attributes

        attr_kwargs: dict[str, Any] = {}
        if session_id:
            attr_kwargs["session_id"] = session_id
        if user_id:
            attr_kwargs["user_id"] = user_id
        if tags:
            attr_kwargs["tags"] = tags

        with propagate_attributes(**attr_kwargs):
            with lf.start_as_current_observation(
                name=name,
                as_type="span",
                input=input,
                metadata=metadata,
            ) as root_span:
                yield root_span
    except Exception as exc:
        logger.debug("LangFuse trace_context error: %s", exc)
        yield _NoOpSpan()


@contextmanager
def span_context(
    _parent: Any,
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: dict | None = None,
) -> Generator:
    """Context manager that creates a child observation in the active OTel context.

    ``_parent`` is accepted for API compatibility but is ignored in v4 —
    nesting is determined by the OTel context stack, not explicit parent refs.
    When LangFuse is disabled, ``_parent`` will be a ``_NoOpSpan`` and
    a no-op stub is returned immediately.

    Usage::

        with span_context(trace, "rag_search", input={"query": q}) as span:
            results = rag.search(...)
            span.update(output=results)
    """
    if isinstance(_parent, _NoOpSpan):
        yield _NoOpSpan()
        return

    lf = get_langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    try:
        with lf.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=input,
            metadata=metadata,
        ) as span:
            yield span
    except Exception as exc:
        logger.debug("LangFuse span_context error: %s", exc)
        yield _NoOpSpan()


def create_generation(
    _parent: Any,
    *,
    name: str,
    model: str = "",
    input: Any = None,
    output: Any = None,
    usage_details: dict[str, int] | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an LLM generation in the current OTel context (non-blocking).

    ``_parent`` is kept for API compatibility but ignored in v4.
    """
    if isinstance(_parent, _NoOpSpan):
        return

    lf = get_langfuse()
    if lf is None:
        return

    try:
        gen = lf.start_observation(
            name=name,
            as_type="generation",
            input=input,
            output=output,
            model=model or None,
            usage_details=usage_details,
            metadata=metadata,
        )
        gen.end()
    except Exception as exc:
        logger.debug("LangFuse create_generation error: %s", exc)


# ---------------------------------------------------------------------------
# No-op stub — used when LangFuse is disabled so callers don't need
# to sprinkle ``if trace:`` checks everywhere.
# Mirrors the subset of LangfuseSpan methods we actually call.
# ---------------------------------------------------------------------------

class _NoOpSpan:
    """Drop-in stub returned when LangFuse is disabled."""

    def update(self, **_kw):
        return self

    def end(self, **_kw):
        return None

    def start_as_current_observation(self, **_kw):
        return _noop_ctx()

    def start_observation(self, **_kw):
        return _NoOpSpan()

    def set_trace_io(self, **_kw):
        return None


@contextmanager
def _noop_ctx() -> Generator:
    yield _NoOpSpan()
