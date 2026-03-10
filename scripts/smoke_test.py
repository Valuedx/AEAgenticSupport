import os
import sys
import logging

# Set up logging to console to see our new logs
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

sys.path.insert(0, os.getcwd())
from config.settings import CONFIG
from config.llm_client import llm_client
from tools.registry import tool_registry
from tools.bootstrap import initialize_tooling

def test_smoke():
    print("--- Environment Check ---")
    print(f"Model: {CONFIG.get('VERTEX_AI_MODEL')}")
    print(f"MCP Enabled: {CONFIG.get('AE_MCP_TOOLS_ENABLED')}")
    
    print("\n--- LLM Check ---")
    try:
        resp = llm_client.chat("Hello!", system="Be brief.")
        print(f"LLM Response: {resp}")
        if len(str(resp)) < 5:
            print("WARNING: LLM response still suspiciously short!")
    except Exception as e:
        print(f"LLM Error: {e}")

    print("\n--- Tool Registration Check ---")
    summary = initialize_tooling()
    print(f"Modules loaded: {len(summary['modules_loaded'])}")
    print(f"Dynamic reload: {summary['dynamic_reload']}")
    
    tools = tool_registry.list_tools()
    print(f"\nTotal tools registered: {len(tools)}")
    
    mcp_tools = [t for t in tools if t.startswith("ae.")]
    print(f"MCP tools found (ae. prefix): {len(mcp_tools)}")
    
    if len(mcp_tools) > 0:
        print("SUCCESS: MCP tools are now visible in the registry.")
        print(f"Common tools example: {mcp_tools[:5]}")
    else:
        print("NOTICE: No MCP tools found. Check if the bridge is working.")
        print(f"Top 10 tools: {tools[:10]}")

if __name__ == "__main__":
    test_smoke()
