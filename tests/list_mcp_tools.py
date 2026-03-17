
import os
import sys
import json

# Ensure local imports work
sys.path.insert(0, os.getcwd())

from tools.bootstrap import initialize_tooling
from tools.registry import tool_registry

def list_mcp_tools():
    print("Initializing tooling...")
    initialize_tooling()
    
    tools = tool_registry.list_tools()
    mcp_tools = []
    
    for tool_name in tools:
        # Check if it's in the registry's base entries or catalog
        entry = tool_registry._catalog.get(tool_name)
        if entry and entry.definition.metadata.get("source") == "mcp":
            mcp_tools.append(tool_name)
    
    mcp_tools = sorted(mcp_tools)
    print(f"\nFound {len(mcp_tools)} MCP tools:")
    for tool in mcp_tools:
        print(f" - {tool}")
    
    with open("mcp_tools.json", "w") as f:
        json.dump(mcp_tools, f, indent=2)
    print("\nSaved tool list to mcp_tools.json")

if __name__ == "__main__":
    list_mcp_tools()
