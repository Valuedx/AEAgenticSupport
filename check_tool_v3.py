from mcp_server.server import mcp

tools = mcp._tool_manager._tools
summary_tool = tools["ae.request.get_summary"]

print(f"Name: {summary_tool.name}")
print(f"Parameters type: {type(summary_tool.parameters)}")
print(f"Annotations type: {type(summary_tool.annotations)}")

try:
    schema = summary_tool.output_schema
    print(f"Output Schema: {schema}")
except AttributeError:
    print("No output_schema attribute")

if hasattr(summary_tool, "model_json_schema"):
    print("Has model_json_schema")
    # print(summary_tool.model_json_schema())
