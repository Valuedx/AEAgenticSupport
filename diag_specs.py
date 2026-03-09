from mcp_server.tool_specs import get_mcp_tool_specs

specs = get_mcp_tool_specs()
spec = specs[0]
print(f"Spec name: {spec.name}")
print(f"Attributes of spec: {dir(spec)}")

try:
    f_tool = spec.fastmcp_tool
    print(f"FastMCPTool type: {type(f_tool)}")
    print(f"FastMCPTool attrs: {dir(f_tool)}")
    if hasattr(f_tool, "output_schema"):
        print(f"FastMCPTool output_schema: {f_tool.output_schema}")
    else:
        print("FastMCPTool MISSING output_schema")
except Exception as e:
    print(f"Error accessing fastmcp_tool: {e}")

try:
    print(f"Spec output_schema: {spec.output_schema}")
except Exception as e:
    print(f"Error accessing spec.output_schema: {e}")
