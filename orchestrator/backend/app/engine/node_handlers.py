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
    """Execute an LLM agent node via the configured provider.

    Renders the system prompt through Jinja2 with context variable injection,
    assembles upstream outputs into the user message, and calls the LLM.
    """
    from app.engine.llm_providers import call_llm
    from app.engine.prompt_template import render_prompt, build_user_message

    config = node_data.get("config", {})
    provider = config.get("provider", "google")
    model = config.get("model", "gemini-2.5-flash")
    raw_prompt = config.get("systemPrompt", "")
    temperature = float(config.get("temperature", 0.7))
    max_tokens = int(config.get("maxTokens", 4096))

    system_prompt = render_prompt(raw_prompt, context)
    user_message = build_user_message(context)

    logger.info(
        "Agent node [%s/%s]: prompt_len=%d, user_msg_len=%d",
        provider, model, len(system_prompt), len(user_message),
    )

    result = call_llm(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    logger.info(
        "Agent node [%s/%s]: tokens in=%d out=%d",
        provider, model,
        result["usage"]["input_tokens"],
        result["usage"]["output_tokens"],
    )

    return result


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
        from app.engine.safe_eval import safe_eval, SafeEvalError

        expr = config["condition"]
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        eval_env = {"output": upstream, "context": context, "trigger": context.get("trigger", {})}
        eval_env.update(upstream)
        try:
            result = bool(safe_eval(expr, eval_env))
        except SafeEvalError as exc:
            logger.warning("Condition expression rejected by safe evaluator: %s", exc)
            result = False
        except Exception:
            result = False
        return {"branch": "true" if result else "false", "evaluated": expr}

    if config.get("strategy") == "waitAll":
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        return {"merged": upstream}

    logger.warning("Logic node '%s' has no handler", label)
    return {"output": None}
