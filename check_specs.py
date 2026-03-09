from mcp_server.tool_specs import get_mcp_tool_specs
specs = get_mcp_tool_specs()
print(f"Type of first spec: {type(specs[0])}")
print(f"Attributes of first spec: {dir(specs[0])}")
