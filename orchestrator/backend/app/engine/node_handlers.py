"""Per-node-type execution handlers.

Each handler receives the node's data dict, the accumulated execution context,
and the tenant_id.  It returns a JSON-serializable output dict that gets stored
in the context keyed by node_id.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def dispatch_node(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    category = node_data.get("nodeCategory", "action")
    handlers = {
        "trigger": _handle_trigger,
        "agent": _handle_agent,
        "action": _handle_action,
        "logic": _handle_logic,
    }
    handler = handlers.get(category, _handle_action)
    return handler(node_data, context, tenant_id)


def _handle_trigger(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Trigger nodes simply pass through whatever payload started the workflow."""
    return {"output": context.get("trigger", {})}


def _handle_agent(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Placeholder for LLM agent execution.

    In production this would call the configured LLM provider (Google, OpenAI,
    Anthropic) with the system prompt + assembled context.
    """
    config = node_data.get("config", {})
    provider = config.get("provider", "google")
    model = config.get("model", "gemini-2.5-flash")
    system_prompt = config.get("systemPrompt", "")

    upstream = {k: v for k, v in context.items() if k.startswith("node_")}

    logger.info(
        "Agent node [%s/%s]: prompt=%s, upstream_keys=%s",
        provider, model, system_prompt[:80], list(upstream.keys()),
    )

    # TODO: Replace with actual LLM API call
    return {
        "provider": provider,
        "model": model,
        "response": f"[STUB] LLM response from {provider}/{model}",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _handle_action(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Execute action nodes: MCP tool calls, HTTP requests, etc."""
    config = node_data.get("config", {})
    label = node_data.get("label", "")

    if config.get("toolName"):
        return _call_mcp_tool(config["toolName"], config.get("parameters", {}), tenant_id)

    if config.get("url"):
        return _call_http(config)

    logger.warning("Action node '%s' has no executable config", label)
    return {"output": None, "warning": "No action configured"}


def _call_mcp_tool(
    tool_name: str, parameters: dict, tenant_id: str
) -> dict[str, Any]:
    """Invoke a tool on the existing MCP server."""
    try:
        resp = httpx.post(
            f"{settings.mcp_server_url}/call-tool",
            json={"tool_name": tool_name, "arguments": parameters},
            headers={"X-Tenant-Id": tenant_id},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        logger.error("MCP tool call failed: %s", exc)
        return {"error": str(exc)}


def _call_http(config: dict) -> dict[str, Any]:
    """Make a generic HTTP request."""
    try:
        resp = httpx.request(
            method=config.get("method", "GET"),
            url=config["url"],
            headers=config.get("headers", {}),
            content=config.get("body", None),
            timeout=30.0,
        )
        return {
            "status_code": resp.status_code,
            "body": resp.text[:10000],
        }
    except httpx.HTTPError as exc:
        logger.error("HTTP request failed: %s", exc)
        return {"error": str(exc)}


def _handle_logic(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Evaluate condition/merge logic nodes.

    For conditions, evaluates a simple expression against the context.
    For merges, aggregates upstream outputs.
    """
    config = node_data.get("config", {})
    label = node_data.get("label", "")

    if "condition" in config:
        expr = config["condition"]
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        try:
            result = bool(eval(expr, {"__builtins__": {}}, {"output": upstream, "context": context}))  # noqa: S307
        except Exception:
            result = False
        return {"branch": "true" if result else "false", "evaluated": expr}

    if config.get("strategy") == "waitAll":
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        return {"merged": upstream}

    logger.warning("Logic node '%s' has no handler", label)
    return {"output": None}
