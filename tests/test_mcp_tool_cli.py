
import os
import sys
import json
import traceback

# Ensure local imports work
sys.path.insert(0, os.getcwd())

from tools.bootstrap import initialize_tooling
from tools.registry import tool_registry

def test_single_tool(tool_name, params=None):
    if params is None:
        params = {}
    
    print(f"\n--- Testing Tool: {tool_name} ---")
    try:
        result = tool_registry.execute(tool_name, **params)
        print(f"Success: {result.success}")
        if result.success:
            # Print a snippet of data
            data_str = json.dumps(result.data, indent=2)
            if len(data_str) > 500:
                print(f"Data: {data_str[:500]}... (truncated)")
            else:
                print(f"Data: {data_str}")
        else:
            print(f"Error: {result.error}")
        return result.success, result.data, result.error
    except Exception as e:
        print(f"Exception: {str(e)}")
        traceback.print_exc()
        return False, {}, str(e)

if __name__ == "__main__":
    initialize_tooling()
    if len(sys.argv) < 2:
        print("Usage: python test_mcp_tool.py <tool_name> [params_json]")
        sys.exit(1)
    
    name = sys.argv[1]
    args = {}
    if len(sys.argv) > 2:
        args = json.loads(sys.argv[2])
    
    test_single_tool(name, args)
