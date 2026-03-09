from mcp_server.server import mcp
tools = mcp._tool_manager._tools
summary_tool = tools["ae.request.get_summary"]
print(f"Type of summary_tool: {type(summary_tool)}")
print(f"Attributes of summary_tool: {dir(summary_tool)}")
if hasattr(summary_tool, "output_schema"):
    print("Has output_schema")
else:
    print("Does NOT have output_schema")
