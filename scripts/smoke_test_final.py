import os
import sys
import logging

sys.path.insert(0, os.getcwd())
from config.settings import CONFIG
from config.llm_client import llm_client
from tools.registry import tool_registry
from tools.bootstrap import initialize_tooling

def test_smoke():
    with open("smoke_final.txt", "w", encoding="utf-8") as f:
        f.write("--- Environment Check ---\n")
        f.write(f"Model: {CONFIG.get('VERTEX_AI_MODEL')}\n")
        f.write(f"MCP Enabled: {CONFIG.get('AE_MCP_TOOLS_ENABLED')}\n")
        
        f.write("\n--- LLM Check ---\n")
        try:
            resp = llm_client.chat("Hello!", system="Be brief.")
            f.write(f"LLM Response: {resp}\n")
            if len(str(resp)) < 5:
                f.write("WARNING: LLM response still suspiciously short!\n")
            else:
                f.write("SUCCESS: LLM response is healthy.\n")
        except Exception as e:
            f.write(f"LLM Error: {e}\n")

        f.write("\n--- Tool Registration Check ---\n")
        summary = initialize_tooling()
        f.write(f"Modules loaded: {len(summary['modules_loaded'])}\n")
        f.write(f"Dynamic reload: {summary['dynamic_reload']}\n")
        
        tools = tool_registry.list_tools()
        f.write(f"\nTotal tools registered: {len(tools)}\n")
        
        mcp_tools = [t for t in tools if t.startswith("mcp__")]
        f.write(f"MCP tools found: {len(mcp_tools)}\n")
        
        if len(mcp_tools) > 0:
            f.write("SUCCESS: MCP tools are now visible in the registry.\n")
        else:
            f.write("NOTICE: No MCP tools found.\n")

if __name__ == "__main__":
    test_smoke()
