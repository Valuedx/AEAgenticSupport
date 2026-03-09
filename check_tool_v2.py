from mcp_server.server import mcp
import json

tools = mcp._tool_manager._tools
summary_tool = tools["ae.request.get_summary"]

print(f"Type: {type(summary_tool)}")
print(f"Name: {summary_tool.name}")

# Check for attributes directly
attrs = ["parameters", "annotations", "description", "output_schema", "meta", "title"]
for attr in attrs:
    val = getattr(summary_tool, attr, "MISSING")
    print(f"{attr}: {type(val) if val != 'MISSING' else 'MISSING'}")

# If it's a pydantic model (likely), check model_fields
if hasattr(summary_tool, "model_fields"):
    print(f"Model fields: {list(summary_tool.model_fields.keys())}")

# Try to see if there's any meta information elsewhere
print(f"All attributes: {dir(summary_tool)}")
