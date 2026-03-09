from __future__ import annotations

from unittest.mock import AsyncMock, patch

from config.settings import CONFIG
from mcp.types import CallToolResult, Tool, ToolAnnotations
from mcp_server.server import mcp
from mcp_server.tool_specs import get_mcp_tool_specs
from tools.registry import ToolRegistry
import tools.mcp_tools as mcp_tools


def test_shared_mcp_specs_expose_structured_schema():
    specs = get_mcp_tool_specs()

    assert len(specs) == 106

    summary_spec = next(spec for spec in specs if spec.name == "ae.request.get_summary")
    restart_spec = next(spec for spec in specs if spec.name == "ae.request.restart_failed")
    assert summary_spec.resolved_title == "Request: Get Summary"
    assert summary_spec.required_params == ["request_id"]
    assert summary_spec.output_schema["type"] == "object"
    assert summary_spec.serialized_annotations["readOnlyHint"] is True
    assert restart_spec.use_when.startswith("The request is in Failure")
    assert restart_spec.parameter_properties["reason"]["description"].startswith("Why a restart is safe now")
    assert restart_spec.input_schema["examples"][0]["dry_run"] is True


def test_server_registers_annotations_and_full_signature():
    tools = mcp._tool_manager._tools

    summary_tool = tools["ae.request.get_summary"]
    snapshot_tool = tools["ae.support.build_case_snapshot"]
    restart_tool = tools["ae.request.restart_failed"]

    assert summary_tool.annotations.title == "Request: Get Summary"
    assert summary_tool.annotations.readOnlyHint is True

    snapshot_props = summary_tool.parameters["properties"] if "ae.request.get_summary" == "ae.support.build_case_snapshot" else snapshot_tool.parameters["properties"]
    assert "include_logs_summary" in snapshot_props
    assert snapshot_props["include_logs_summary"]["type"] == "boolean"
    assert snapshot_props["include_logs_summary"]["description"].startswith("Whether to include")
    assert restart_tool.parameters["properties"]["reason"]["description"].startswith("Why a restart is safe now")
    assert restart_tool.parameters["examples"][0]["request_id"] == "REQ-10421"


def test_main_app_bridge_preserves_mcp_metadata_and_optional_args():
    registry = ToolRegistry()
    original_flag = CONFIG.get("AE_MCP_TOOLS_ENABLED", False)
    CONFIG["AE_MCP_TOOLS_ENABLED"] = True

    try:
        with patch.object(mcp_tools, "tool_registry", registry):
            mcp_tools._register_mcp_tools()
    finally:
        CONFIG["AE_MCP_TOOLS_ENABLED"] = original_flag

    tool = registry.get_tool("ae.support.build_case_snapshot")

    assert tool is not None
    assert tool.required_params == ["request_id"]
    assert "include_logs_summary" in tool.parameters
    assert tool.parameters["include_logs_summary"]["type"] == "boolean"
    assert tool.metadata["title"] == "Support: Build Escalation Snapshot"
    assert tool.metadata["annotations"]["readOnlyHint"] is True
    assert tool.metadata["output_schema"]["type"] == "object"
    assert tool.metadata["structured_output"] is True

    restart_tool = registry.get_tool("ae.request.restart_failed")
    assert restart_tool is not None
    assert restart_tool.use_when.startswith("The request is in Failure")
    assert restart_tool.avoid_when.startswith("The failure was caused")
    assert restart_tool.input_examples[0]["case_id"] == "INC-4201"
    assert restart_tool.parameters["reason"]["description"].startswith("Why a restart is safe now")


def test_main_app_bridge_can_catalog_and_execute_remote_mcp_tools():
    registry = ToolRegistry()
    original_flag = CONFIG.get("AE_MCP_TOOLS_ENABLED", False)
    original_url = CONFIG.get("AE_MCP_SERVER_URL", "")
    original_transport = CONFIG.get("AE_MCP_SERVER_TRANSPORT", "streamable-http")
    original_headers = CONFIG.get("AE_MCP_SERVER_HEADERS_JSON", "")
    original_timeout = CONFIG.get("AE_MCP_SERVER_TIMEOUT_SECONDS", "30")
    CONFIG["AE_MCP_TOOLS_ENABLED"] = True
    CONFIG["AE_MCP_SERVER_URL"] = "http://mcp-host:8000/mcp"
    CONFIG["AE_MCP_SERVER_TRANSPORT"] = "streamable-http"
    CONFIG["AE_MCP_SERVER_HEADERS_JSON"] = ""
    CONFIG["AE_MCP_SERVER_TIMEOUT_SECONDS"] = "30"

    remote_tool = Tool(
        name="ae.request.get_summary",
        title="Request: Get Summary",
        description="Fetch a remote request summary.",
        inputSchema={
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "AutomationEdge request ID.",
                }
            },
            "required": ["request_id"],
        },
        outputSchema={"type": "object"},
        annotations=ToolAnnotations(readOnlyHint=True, title="Request: Get Summary"),
        _meta={
            "category": "request_read",
            "app_category": "status",
            "tier": "read_only",
            "safety": "safe_read",
            "always_available": True,
            "latency_class": "medium",
            "structured_output": True,
            "tags": ["request", "summary"],
            "use_when": "You need a one-shot remote summary.",
            "avoid_when": "You already have the full request snapshot.",
            "input_examples": [{"request_id": "REQ-9001"}],
        },
    )
    remote_call = AsyncMock(
        return_value=CallToolResult(
            content=[{"type": "text", "text": "ok"}],
            structuredContent={"request_id": "REQ-9001", "status": "Failure"},
            isError=False,
        )
    )

    try:
        with (
            patch.object(mcp_tools, "tool_registry", registry),
            patch.object(mcp_tools, "_discover_remote_mcp_tools", return_value=(remote_tool,)),
            patch.object(mcp_tools, "_call_remote_mcp_tool", remote_call),
        ):
            mcp_tools._register_mcp_tools()
            result = registry.execute("ae.request.get_summary", request_id="REQ-9001")
    finally:
        CONFIG["AE_MCP_TOOLS_ENABLED"] = original_flag
        CONFIG["AE_MCP_SERVER_URL"] = original_url
        CONFIG["AE_MCP_SERVER_TRANSPORT"] = original_transport
        CONFIG["AE_MCP_SERVER_HEADERS_JSON"] = original_headers
        CONFIG["AE_MCP_SERVER_TIMEOUT_SECONDS"] = original_timeout

    tool = registry.get_tool("ae.request.get_summary")

    assert tool is not None
    assert tool.required_params == ["request_id"]
    assert tool.metadata["mcp_connection_mode"] == "remote"
    assert tool.metadata["mcp_transport"] == "streamable-http"
    assert tool.metadata["annotations"]["readOnlyHint"] is True
    assert tool.input_examples[0]["request_id"] == "REQ-9001"
    assert result.success is True
    assert result.data["request_id"] == "REQ-9001"
    assert result.data["status"] == "Failure"
    remote_call.assert_awaited_once_with("ae.request.get_summary", {"request_id": "REQ-9001"})
