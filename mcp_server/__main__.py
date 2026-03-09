"""
Entry point for the AutomationEdge MCP Server.

Usage:
    python -m mcp_server                     # stdio transport (default)
    python -m mcp_server --transport sse     # SSE transport
    python -m mcp_server --transport streamable-http  # Streamable HTTP transport
"""
from __future__ import annotations

import argparse
import logging
import sys

from mcp_server.config import MCP_CONFIG


def main() -> None:
    parser = argparse.ArgumentParser(description="AutomationEdge MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=MCP_CONFIG.get("MCP_TRANSPORT", "stdio"),
        help="MCP transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=MCP_CONFIG.get("MCP_HOST", "127.0.0.1"),
        help="Host for HTTP/SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=MCP_CONFIG.get("MCP_PORT", 8000),
        help="Port for HTTP/SSE transport (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        default=MCP_CONFIG.get("LOG_LEVEL", "INFO"),
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    from mcp_server.server import mcp

    kwargs: dict = {"transport": args.transport}
    if args.transport in ("sse", "streamable-http"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(**kwargs)


if __name__ == "__main__":
    main()
