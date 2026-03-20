"""Jinja2 prompt templating with context variable injection.

System prompts can reference upstream node outputs and trigger data using
Jinja2 syntax:

    You are an IT support assistant.
    The user reported: {{ trigger.user_query }}
    Request status: {{ node_1.output.status }}
    Logs summary: {{ node_2.body | truncate(500) }}

All keys in the execution context are available as top-level variables.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from jinja2 import Environment, BaseLoader, TemplateSyntaxError, Undefined

logger = logging.getLogger(__name__)


class _PermissiveUndefined(Undefined):
    """Returns empty string for missing variables instead of raising."""

    def __str__(self) -> str:
        return ""

    def __iter__(self):
        return iter([])

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name: str) -> "_PermissiveUndefined":
        return _PermissiveUndefined()

    def __getitem__(self, name: str) -> "_PermissiveUndefined":
        return _PermissiveUndefined()


_env = Environment(
    loader=BaseLoader(),
    autoescape=False,
    undefined=_PermissiveUndefined,
    keep_trailing_newline=True,
)


class _DotDict(dict):
    """Dict subclass that allows attribute-style access for Jinja2 templates."""

    def __getattr__(self, name: str) -> Any:
        try:
            val = self[name]
            return _wrap(val)
        except KeyError:
            return _PermissiveUndefined()


def _wrap(value: Any) -> Any:
    """Recursively wrap dicts for dot-access in templates."""
    if isinstance(value, dict):
        return _DotDict(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def render_prompt(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the execution context.

    Wraps dict values so they're dot-accessible in templates:
        {{ node_1.response }} instead of {{ node_1["response"] }}
    """
    if not template_str or ("{{" not in template_str and "{%" not in template_str):
        return template_str

    safe_context = {k: _wrap(v) for k, v in context.items()}

    try:
        tmpl = _env.from_string(template_str)
        return tmpl.render(**safe_context)
    except (TemplateSyntaxError, Exception) as exc:
        logger.warning("Prompt template rendering failed: %s", exc)
        return template_str


def build_user_message(context: dict[str, Any]) -> str:
    """Assemble upstream node outputs into a structured user message
    that the LLM can reason over."""
    parts: list[str] = []

    trigger = context.get("trigger")
    if trigger:
        parts.append(f"**Trigger input:**\n```json\n{json.dumps(trigger, indent=2, default=str)}\n```")

    for key, value in context.items():
        if not key.startswith("node_"):
            continue
        summary = json.dumps(value, indent=2, default=str)
        if len(summary) > 2000:
            summary = summary[:2000] + "\n... (truncated)"
        parts.append(f"**Output of {key}:**\n```json\n{summary}\n```")

    if not parts:
        return "No upstream data available. Please respond based on your system instructions."

    return "\n\n".join(parts)
